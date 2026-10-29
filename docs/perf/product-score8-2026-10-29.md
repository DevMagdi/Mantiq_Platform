# Load test — Product Score-8 (2026-10-29): قبل/بعد

خام: `docs/qa/load/raw/s8-*.csv`. نفس عتاد الجولات السابقة: **2 vCPU / 4GB**، PostgreSQL 16.2 (pgserver)،
uvicorn عاملان، وLocust على نفس الجهاز (يشارك الـCPU مع الخادم — جزء من سبب الأرقام، ويُذكر لا يُخفى).

## ما تغيّر في الكود (C1)
- فهارس مركّبة (migration `0011`): `ledger_entries(posted_at, entry_no)` لقائمة الدفتر، و`requests(status, last_activity_at)` لقوائم الطلبات.
- الصندوق: من ~10 استعلامات SUM منفصلة إلى تجميع واحد مجمّع (method × direction + نوافذ اليوم/الشهر بشرط CASE).

## الأوامر
```bash
cd backend && .venv/bin/python scripts/load/pg_start.py /tmp/s8pg   # + CREATE DATABASE brokerhub_load
export DATABASE_URL="postgresql+asyncpg://postgres@/brokerhub_load?host=/tmp/s8pg" INTEGRATIONS_DRIVER=sim ENV=dev STORAGE_DIR=/tmp/s8-objects
.venv/bin/python -m alembic -c alembic.ini upgrade head   # يشمل 0011
.venv/bin/python -m app.seed
PYTHONPATH=. .venv/bin/python scripts/load/erp_volume.py --rows 50000 --clients 200   # ledger_after=50004
PYTHONPATH=. .venv/bin/python scripts/load/r12_volume.py --requests 20000              # requests_total=20024
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8023 --workers 2
.venv/bin/locust -f scripts/load/erp_locustfile.py  --headless -u 20 -r 5  -t 3m --host http://127.0.0.1:8023 --csv docs/qa/load/raw/s8-erp   --only-summary
LOAD_MODE=inbox .venv/bin/locust -f scripts/load/r12_locustfile.py --headless -u 50 -r 10 -t 3m --host http://127.0.0.1:8023 --csv docs/qa/load/raw/s8-inbox --only-summary
```

## النتائج (p95)

| المسار | قبل | بعد | الحكم |
|---|---|---|---|
| `GET /finance/ledger` | 750ms (R11: ‏50k قيد، عامل واحد) · 1900ms (R12: ‏100k قيد) | **1000ms** عند 50k قيد/عاملين | ❌ فوق بوابة 600ms على هذا العتاد |
| `GET /finance/spend-by-period` | 530ms (R11) · 1400ms (R12@100k) | **730ms** عند 50k | ❌ فوق 600ms هنا |
| `GET /finance/cashbox` | 3400ms (R12@100k) | **1200ms** عند 50k (تجميع واحد) | تحسّن واضح، ما زال بطيئًا في المزيج |
| `GET /executive/dashboard` | 7100ms (R12@100k) | **2400ms** عند 50k | تحسّن، ثقيل بنيويًا (تجميعات كثيرة) |
| `GET /requests` (قائمة) | 800ms (R12، ‏20k طلب) | **1100ms** عند 20k طلب/50 مستخدم | ❌ فوق بوابة 400ms هنا |
| أخطاء/5xx | 0 | **0** (3660 طلب ERP + 7980 قراءة) | ✅ |

## القرار الصادق
الفهارس وتجميع الصندوق حسّنا مسارات المال مقارنة بقياس R12 عند ضعف الحجم تقريبًا، لكن **بوابات الزمن
(600ms للدفتر، 400ms لقائمة الطلبات) ما زالت FAIL on this hardware**: عاملان وLocust نفسه يتقاسمون
2 vCPU، والمزيج يشمل `executive/dashboard` الثقيل. لم تُخفَّض أي بوابة. إعادة القياس على staging
بـ4+ أنوية وLocust من جهاز منفصل هي خطوة الـ Pilot الأولى قبل أي ادعاء نجاح أدائي — الأوامر أعلاه
حرفية لهذا الغرض. ما ثبت وظيفيًا: الحجم (50k قيد + 20k طلب)، صفر 5xx، والعزل والـvoid↔قسط لم يتغيرا
(اختبارات pytest + تشغيلات R12 السابقة).

## جولة الإصلاح (نفس اليوم، بعد طلب المالك: «صلّح دول بدقة»)

ثلاثة أسباب جذرية وجدت بالقياس الفردي أولًا (solo) ثم أُصلحت في الكود — لم تُخفَّض أي بوابة:

1. **قائمة الطلبات**: كانت تجلب رسائل كل صف على حدة (N+1، ‏25 استعلامًا للصفحة) ثم خمس
   رحلات `selectinload`، وفوقها سلاسل eager غير مرئية للسيريالايزر (جهات اتصال العميل،
   صلاحيات/نطاقات المُسند إليه). الإصلاح: استعلام نافذة واحد لآخر رسالة لكل طلب
   (`latest_messages`)، و`joinedload` للعلاقات مع `noload` للسلاسل غير المعروضة.
   النتيجة: من 9 استعلامات/81ms إلى استعلامين/28ms لقائمة من 25 صفًا (قياس مباشر على نفس القاعدة).
2. **`/executive/dashboard`**: `sales_performance` كان يُهيدر كل إيصال/وثيقة MTD ككائنات ORM
   ويجمع في بايثون. الإصلاح: تجميعان SQL مجمّعان بـ`client_id` يُسقطان على خريطة المالكين المرئيين.
3. الفهارس المركّبة من `0011` كما هي.

### إعادة القياس بنفس الأوامر والحجم (الخام: `docs/qa/load/raw/s9b-*`)

| المسار | قبل الإصلاح | بعد الإصلاح | البوابة | الحكم |
|---|---|---|---|---|
| `GET /finance/ledger` (ERP ‏20 مستخدم) | p95 ‏1000ms | **p95 ‏160ms** | ≤600ms | ✅ |
| `GET /requests` (Inbox ‏50 مستخدم) | p95 ‏1100ms | **p95 ‏250ms** | ≤400ms | ✅ |
| `GET /finance/cashbox` | p95 ‏1200ms | p95 ‏180ms | — | تحسّن |
| `GET /executive/dashboard` | p95 ‏2400ms | p95 ‏330ms | — | تحسّن |
| أخطاء/5xx | 0 | **0** (6114 طلب ERP + ‏10897 قراءة Inbox) | 0 | ✅ |

نفس التحفظ المنهجي يبقى مكتوبًا: Locust والخادم وقاعدة البيانات على جهاز واحد بـ2 vCPU،
لذا هذه الأرقام أرضية أداء لا سقفها؛ إعادة القياس على staging منفصل ما زالت خطوة الـpilot الأولى.
الفارق أن البوابتين الآن خضراوان **حتى على هذا العتاد المعادي**.

## اللقطات قبل/بعد

`docs/screenshots/score8/` — خمس شاشات (Dashboard، Inbox، ‏/erp، ملف عميل، Login)
قبل (واجهة `main`) وبعد (واجهة السبرينت) على نفس البيانات والـAPI، مع تركيب مقارن في
`compare/` وطريقة الالتقاط موثقة في `docs/screenshots/score8/README.md`.
