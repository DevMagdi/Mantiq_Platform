# Runbook — Broker Hub

موجه لمن يشغّل النظام: تشغيل، مراقبة، حوادث، وصلاحيات. كل أمر هنا نصفيًا قابل للنسخ.

## 1) التشغيل

### حاويات (المسار الأساسي)

```bash
cp infra/.env.example infra/.env            # JWT_SECRET إلزامي في أي بيئة مشتركة
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml exec backend python -m app.seed   # بيانات تجريبية فقط للعرض
curl -fsS http://localhost:8000/healthz; echo
curl -fsS http://localhost:8000/readyz; echo
```

- `migrate` مهمة لمرة واحدة تخرج بنجاح؛ إذا فشلت فلن يبدأ `backend` (بوابة `service_completed_successfully`).
- الوسائط على وحدة `objectsdata` (`STORAGE_DIR=/srv/objects`). لاستخدام S3/MinIO خارجي: `STORAGE_BACKEND=s3`
  و`S3_ENDPOINT_URL`/`S3_BUCKET`/المفاتيح — الصور الرسمية لـMinIO لم تعد قابلة للسحب المجهول (ADR-020).
- الواجهة على `WEB_PORT` (5173) تمرّر `/api` للـAPI عبر nginx؛ لا يوجد أصل API مضمّن في الحزمة.

### بدون حاويات

```bash
cd backend && pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head && python -m app.seed && uvicorn app.main:app --port 8000
cd ../frontend && npm ci && npm run codegen:api && VITE_API_ORIGIN=http://127.0.0.1:8000 npm run dev
```

## 2) المراقبة

| إشارة | مكان | معنى |
| --- | --- | --- |
| `GET /healthz` | :8000 | العملية للحية (لا يلمس قاعدة بيانات) |
| `GET /readyz` | :8000 | `SELECT 1` — 503 مع `not_ready` عند فقدان القاعدة |
| `GET /metrics` | :8000 | Prometheus نصي: `broker_hub_http_requests_total{method,route,status}` + `..._duration_seconds` + `..._in_flight_requests`. التسمية `route` قالب مسار لا URL خام. في التطوير مفتوح؛ مع `METRICS_TOKEN` يحتاج `Authorization: Bearer`؛ بدونه في الإنتاج 403 `metrics_disabled`. مقاييس الـworker ليست هنا عمداً (انظر ADR-011) |
| بريد تجريبي | mailpit على :8025 | كل إشعار بريدّي في stack المحلي يظهر هناك؛ لا SMTP خارج الجهاز. `SMTP_URL=smtp://mailpit:1025` |
| logs | stdout JSON (structlog) | حقول ثابتة: `method path status ms cid driver env`؛ `ms` هو زمن المعالجة بالمللي ثانية |
| طابور Celery | `celery -A app.workers.celery_app:celery_app inspect active` | إن كان فارغًا والمهام تتراكم فالعامل ميت |
| مهمات مجدولة | `celery ... inspect scheduled` | المفروض 4 tasks — انظر §4 |

الجدول الزمني لـ beat (من `app/workers/celery_app.py`):

| المهمة | التكرار | الوظيفة |
| --- | --- | --- |
| `broker_hub.sla_sweep` | كل 60 ثانية | يعلّم `is_overdue` ويحسب «متأخر N س» ويبعث تنبيهات |
| `broker_hub.lead_followup_reminders` | 8:00 و14:00 | تذكيرات متابعة الـLeads |
| `broker_hub.purge_expired_recordings` | 3:15 | حذف تسجيلات تجاوزت `RECORDING_RETENTION_DAYS` |
| `broker_hub.cleanup_tmp_files` | 4:30 | تنظيف ملفات الاستيراد المؤقتة |

## 3) الترقية المخططة (Migrations)

- لا تُحرَّر migration مُدمجة أبدًا؛ أضف واحدة جديدة (`alembic revision -m "..."`). الاستثناء الوحيد
  المسجَّل هو 0001/0002: لم تكونا قادرتين على التشغيل على PostgreSQL إطلاقًا، فلم تُطبَّقا على أي قاعدة،
  والتصحيح كان مستحيلًا في migration لاحقة — التفاصيل والتبرير في `docs/decisions/014-*.md` (ADR-014).
- 0001 = الأساس الكامل (33 جدولًا بترتيب تبعي لا أبجدي: `users` قبل `clients`، وقيد `clients.source_lead_id`
  يُضاف بعد وجود الجدولين لأن حلقة `leads ↔ clients` لا تُعبَّر داخل `CREATE TABLE` على PostgreSQL)؛
  0002 = RLS + فهارس جزئية (PostgreSQL فقط، تقفز على SQLite)؛ 0003 = سياسة `FOR ALL` واحدة لكل جدول
  (تغطي `INSERT` أيضًا) + بند البوابة `app.scope_client_id` + دور `brokerhub_app`.
- لا يتجاوز `revision` 32 حرفًا: `alembic_version.version_num` هو `VARCHAR(32)`، ومخالفته تُفشِل آخر خطوة
  من الترقية بعد تنفيذ الـDDL كله. البوابة: `tests/test_migrations.py::test_every_revision_id_fits_the_version_table`.
- قبل الدمج لا تُصدَّق السلسلة يدويًا فقط — `tests/test_migrations.py` يبني قاعدة PostgreSQL فارغة ويشغّل
  `upgrade head ← downgrade base ← upgrade head` ثم يقارن المخطط الناتج بنماذج `Base.metadata`. يدويًا على
  نسخة إنتاج: `alembic upgrade head` (أو `docker compose run --rm migrate`).
- للنزول الآمن: فضّل إضافة column nullable ثم backfill ثم NOT NULL، بدل downgrade كامل.

## 4) SLA: كيف تُحسب ومتى «تظلم»

1. الأولوية تأتي من `sla_policies` (جدول في القاعدة، لا ثابت في الكود): عاجل 30/120، عالية 60/240،
   عادية 240/1440، منخفضة 600/2880 دقيقة.
2. العدّاد يعمل داخل ساعات العمل من `business_hours` (الجمعة والسبت مغلقان في مصر):
   `app/domain/sla.py` + `WEEKEND_DAYS=[4,5]`.
3. `sla_due_at` تُحسب عند الإنشاء، وتُعاد حسابتها إذا تغيّرت الأولوية (خدمة `requests_svc.update`).
4. `is_overdue` لا يحدّثها الطلب نفسه بل `sla_sweep` — لذا إذا تأخّر worker فلن تظهر «متأخر» حتى لو
   انتهى الوقت. أول تشخيص عند شكوى «لا يوجد أحمر»: تحقق من `inspect scheduled` و`status=200` لمهة الـsweep.
5. أول رد (`first_response_at`) يُختم عند أول رد من موظف، ومنه يشتق «متوسط زمن أول رد» في اللوحة.

## 5) الصلاحيات والعزل (عند شكوى «فلان يرى أكثر/أقل»)

```sql
-- ما الأدوار الممنوحة لهذا المستخدم فعليًا؟
select u.email, r.code, string_agg(p.code, ', ' order by p.code)
from users u join roles r on r.id = u.role_id
left join role_permissions rp on rp.role_id = r.id
left join permissions p on p.id = rp.permission_id
where u.email = 'sara.elsayed@brokerhub.test' group by 1,2;

-- هل له user_scopes تضيّق النطاق (مدير حساب/خدمة عملاء)؟
select s.scope_type, s.scope_id, c.name from user_scopes s left join clients c on c.id = s.scope_id
where s.user_id = (select id from users where email = 'cs@brokerhub.test');
```

- المندوب: `owner` scope (leads به `assigned_to`، عملاء بـ `account_owner_id`)؛ id خارج النطاق ⇒ **404**.
- المدير/أدمن: `all`؛ المندوب `owner`.
- الشبكة الثانية موصولة الآن (ADR-014): `app/core/rls.py` يثبّت `app.bypass_rls` / `app.scope_user_id` /
  `app.scope_client_id` على كل معاملة تفتحها جلسة الطلب (خطّاف `after_begin`)، فيسري حتى على استعلام نسي
  فلتر المستودع. الاختيار ليس `SET LOCAL` عاريًا لأن نقاط النهاية تعمل `commit` في منتصف الطلب — بل إعادة
  تطبيق عند كل `begin`؛ والقيم المعامة لا تبقى على اتصال الـpool عند انتهائه (بوّابتان في
  `tests/test_rls_postgres.py` تُثبتان الأمرين على PostgreSQL حقيقي).
- **السياسات لا تسري على superuser**، ولو مع `FORCE ROW LEVEL SECURITY`. لذا لا معنى للشبكة ما لم يتصل
  التطبيق بدور `NOBYPASSRLS`: 0003 ينشئ `brokerhub_app` (بلا LOGIN وبمنح بيانات فقط). للتحويل: امنحه كلمة
  مرور ثم بدّل `DATABASE_URL` إليه — `ALTER ROLE brokerhub_app PASSWORD '…';` ثم
  `DATABASE_URL=postgresql+asyncpg://brokerhub_app:…@postgres:5432/brokerhub`. لا تُنشِئ الأدوار يدويًا في
  بيئة لا تملك فيها `CREATEROLE`: الترقية تُكمل وتُنبّه (المهاجرة تتخطّى إنشاء الدور بهدوء).
- فحص من `psql` (المتوقّع `0` لغير المالك، ثم `2` بعد مفتاح التجاوز):

```sql
BEGIN;
SET LOCAL ROLE brokerhub_app;
SELECT set_config('app.bypass_rls', 'off', true);
SELECT set_config('app.scope_user_id', '00000000-0000-0000-0000-000000000000', true);
SELECT count(*) FROM clients;  -- ⇒ 0
SELECT set_config('app.bypass_rls', 'on', true);
SELECT count(*) FROM clients;  -- ⇒ كل الصفوف (نطاق مدير)
ROLLBACK;
```
- تغيير صلاحيات دور يرفع `refresh_token_version` لكل مستخدميه ⇒ جلسات تُنهى. توقّع إعادة دخول.
- تغيير كلمة مرور من شاشة المستخدمين ينهي الجلسات القديمة أيضًا.

### مكتب المبيعات: الاستيراد، وإعادة التوزيع، والمقاعد

| سؤال عند التشغيل | أين الجواب | السلوك المتوقع |
|---|---|---|
| «البيّاع رفع ملف Excel؟» | `POST /leads/import` خلف `leads.view_all` (`backend/app/api/v1/endpoints/leads.py`) | 403 `details.missing = ["leads.view_all"]` — والزر في `KanbanPage` لا يُعرض أصلًا لنفس السبب (إخفاءٌ للواجهة، لا إذن لها) |
| «زر إعادة التوزيع حرّك 25 فقط من 63» | `repo.unassigned_ids` (سقف 2000 صفاً) + `POST /leads/assign-bulk` | لا صفحة ولا حلقة `PATCH`: طلب واحد، و`assigned` في الردّ = عدد الحوض في البطاقة، وسطر `lead.assigned_bulk` واحد للتدقيق وسطر `assignment` لكل ليد |
| «فلان موقوف ولا يُحمَّل شغلاً» | `RepWorkloadOut.is_active` ⇒ `rows.filter((row) => row.is_active)` في منتقي `SalesTeamPage` | الموقوف يبقى على لوحة الحِمل (أعماله الحقيقية لا تختفي) ولا يظهر في المنتقي؛ لو أُرسِل صراحةً في `assign-bulk` ⇒ 422 `Target rep does not exist or is inactive` (الفحص في الخادم، لا في الواجهة) |
| «الملف كبير / مرفوض» | `MAX_ROWS = 5000` · `MAX_FILE_BYTES = 5_000_000` · `.xlsx` أو `.csv` (`backend/app/services/lead_import.py`) | 422 قبل القراءة. داخل الملف: بلا موبايل = error؛ نوع تأمين أو بيّاع لا يطابق = warning ويبقى الصف غير مسند؛ موبايل موجود في lead نشط = skipped ولا يُنشَف |
| «مقعد جديد بلا كلمة مرور» / «أوقفوا حسابًا» | `POST/PATCH /api/v1/sales-team/members[/{user_id}]` | كلمة المرور يولّدها الخادم وتُعرض في الردّ مرة واحدة ولا تُكتب في `audit_logs` (ADR-017 §5)؛ `{"is_active": false}` تمنع تسجيل الدخول (403) وتُخرج المقعد من منتقي التوزيع |

### الرايات (Feature flags) — إطفاء ميزة بدون نشر

تُقرأ من جدول `feature_flags` في كل طلب (ADR-008)، وتُقلَّب `PATCH /api/v1/feature-flags/{key}`
(`{"enabled": true|false}`، للادمن، بسطر في `audit_logs`).

| الراية | ما تفتحه | سلوكها عند الإطفاء |
|---|---|---|
| `phase2.enabled` | شاشات الوثائق/الأقساط/العمولات/الإصدار/التقارير + `POST /exports/insurer-performance` + `GET /reports/*` | 503 `feature_disabled` مع `details.flag` (وليس 403: «الميزة مطفأة» حالة خدمة) |
| `ai.enabled` | `POST /api/v1/ai/calls/{id}/transcribe` · `POST /api/v1/ai/requests/{id}/summarize` · `POST /api/v1/ai/classify` | نفس الـ503. `GET /api/v1/ai/status` يبقى مقروءًا بدون الراية حتى تُخفي الواجهة زر «تلخيص آلي» (ADR-013) |
| `whatsapp.enabled` · `telephony.enabled` | — | **غير مطبقتين في الكود بعد**: الرايتان مزروعتان في الجدول فقط، ومسارا الـwebhook لا يقرأهما. موثّق كفجوة في `STATUS.md` (سطر 11) |


### إعدادات التكاملات (الشاشة، الكاش، الأسرار)

الشاشة `/settings` → تبويب «التكاملات» تكتب في `integration_configs`، والسائقون يقرأون عبر
`registry.prime()` — كاش داخل العملية لا مصدر ثانٍ للحقيقة (ADR-016 §1). عملياً:

| الحالة | ما يحدث | ما يُطلب منك |
|---|---|---|
| حفظت من الشاشة | السطر يُكتب ثم تُحمَّل القيم في ذاكرة **العملية التي خدمت الطلب** | لا شيء؛ الاختبار التالي يخرج بالقيمة الجديدة |
| نشر متعدد العمال (`uvicorn --workers N`) | بقية العمليات تبقى على نسختها حتى تُعاد | بعد تغيير يمسّ الأمان (`verify_token`, `webhook_secret`, `app_secret`) أو قبل التحوّل إلى الحيّ: `docker compose restart api` |
| `verify_token` مضبوط من الشاشة | مصافحة Meta (`GET /webhooks/whatsapp?hub.verify_token=…`) تقارنه بخريطة **الأسرار** | لا تضبطه في `config`؛ لا يوجد مثل هذا الحقل. بلا قيمة محفوظة الرفض 403 برسالة «اضبط Verify Token في /settings أولًا» |
| تدوير `SECRETS_ENCRYPTION_KEY` | اضبط `SECRETS_ENCRYPTION_KEY_OLD` بالمفتاح القديم مدة التدوير: قراءة با القديمة وكتابة بالجديد | بعد إعادة حفظ الأسرار من الشاشة **احذف المتغير القديم**؛ النافذة ليست دائمة |
| مفتاح بلا قيمة في `ENV=prod` | الإقلاع تفشل عمداً | `SECRETS_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")` |
| «اختبار الاتصال» يرفض | 429 مع `details.retry_after_seconds` (6/دقيقة/مستخدم) | انتظر الثواني؛ لا تحتاج إعادة تشغيل |

لا يوجد مسار يقرأ سرّاً إلى المتصفح: `GET /settings/integrations/{provider}` يعطي
`{"set": true, "display": "••••••••"}` فقط، والحقل الفارغ في الحفظ = «لا تغيير»، و`null` = حذف (ADR-016 §2).
كل حفظ وكل اختبار وكل تبديل إلى الوضع الحيّ يكتب صفاً في `audit_logs` بأسماء الحقول لا بقيمها.

بوابة «كل النقاط شغّالة»: `python scripts/smoke_api.py` — قاعدة مؤقتة مُزروعة ثم كل `(path, method)` في
وثيقة OpenAPI (125 نداءً؛ 2 مستثنيان بسبب مكتوب). تُشغَّل في CI بعد بوابات التغطية.


## 6) الوسائط والتسجيلات

- كل مرفق وارد من مزوّد خارجي يُنسخ إلى دلائنا أولًا (`stage_inbound_media`)؛ لا نعرض روابط المزوّد.
- رابط التشغيل موقّع وقصير العمر: `RECORDING_SIGNED_URL_TTL_SECONDS` (120 ثانية افتراضيًا). «403/404 بعد
  قليل من الفتح» يعني أن الرابط انتهت صلاحيته — الواجهة تطلب رابطًا جديدًا عند كل ضغطة، لا تُخزّنه.
- كل استماع يسجّل `call.recording_listened` في `audit_logs` مع `ttl_seconds` و`recording_consent`.
- التعديل السريع عند ضياع كائن: مع `STORAGE_BACKEND=s3` استخدم
  `aws s3api head-object --bucket brokerhub --key <object_key>`؛ ومع المخزن المحلي ابحث في وحدة
  `objectsdata` (`docker compose exec backend ls -l /srv/objects/<prefix>`).

## 6.1) القنوات الحيّة: واتساب (Meta Cloud API) ومكالمات الموبايل

**R9 — الإنتاج حيّ فقط.** في `ENV=prod`:

- `INTEGRATIONS_DRIVER` يُفرض `live`، وكتابته `sim` صريحة **تُفشل الإقلاع** (لا تجاوز صامت).
- لا يوجد سائق محاكاة في العملية إطلاقًا: `WhatsAppSim` و`MobileCallsSim` يرفضان البناء خارج `ENV=dev|test`.
- `registry.assert_production_ready()` يفحص الأسرار الحيّة عند الإقلاع، وإن نقص شيء **تفشل العملية** برسالة
  عربية تسمّي الناقص (`WHATSAPP_ACCESS_TOKEN`، `TELEPHONY_WEBHOOK_SECRET` …) — لا سقوط صامت على المحاكاة.
- مسارات `/api/v1/dev/sim*` **غير موجودة في بناء الإنتاج** (404 من الموجّه؛ اللوحة تُركّب فقط في dev/test).
- أسرار التطوير المنشورة في المستودع (`dev-app-secret`, `dev-verify-token`, `dev-telephony-secret`) تُفرَّغ
  تلقائيًا في الإنتاج، حتى لا يُوقّع أي حدث بمفتاح معروف.

في dev/test يبقى الوضع الافتراضي `INTEGRATIONS_DRIVER=sim` (كل شيء يُسجَّل ولا يخرج). الانتقال إلى `live`
لا يحتاج نشراً جديداً: الشاشة `/settings` هي المصدر (صف `integration_configs` يتغلّب على متغيّر البيئة،
ADR-016)، وبعد أي حفظ تُشغَّل `registry.prime()` — أي أن السائق يُعاد بناؤه بلا إعادة تشغيل. إن كان لديك
أكثر من عامل API، أعد تشغيل العمال بعد الحفظ (المذكرة في `registry.py`).

حالة المزوّد في الشاشة ثلاث كلمات فقط: **`Live`** (حيّ)، **«جاهز»** (مضبوط واختباره أخضر وينتظر المفتاح)،
**«غير مُعد»** — ولا يوجد خيار «محاكاة» في واجهة الإنتاج.

### أ) واتساب — خطوات Meta بالترتيب

1. **التطبيق**: من <https://developers.facebook.com/apps> أنشئ تطبيقاً من نوع *Business*، أضف منتج
   **WhatsApp**، واربطه بـWABA (WhatsApp Business Account).
2. **الأرقام**: من *WhatsApp → API Setup* خُذ **Phone Number ID** (رقم داخلي، ليس رقم الهاتف) و
   **WhatsApp Business Account ID** (WABA ID). أضف رقم المرسل ووثّقه إن لم يكن تجريبياً.
3. **التوكن**: استخدم *System User* دائم من Business Settings بصلاحية `whatsapp_business_messaging` +
   `whatsapp_business_management`، لا التوكن التجريبي (24 ساعة).
4. **App Secret**: *Settings → Basic → App Secret*. هذا هو مفتاح توقيع `X-Hub-Signature-256`؛ بدونه كل
   حدث وارد يُرفض بـ403 (وهذا هو السلوك الصحيح).
5. **Verify Token**: اختَر سلسلة عشوائية طويلة أنت نفسك، واكتب نفسها في مكانين: شاشة `/settings` وهذه
   الخطوة في Meta (*Webhooks → Verify token*).
6. **Callback URL**: `https://<نطاق-الوسيط>/api/v1/webhooks/whatsapp`، واشترك في حقل `messages`
   (و`message_template_status_update` إن كنت تستخدم القوالب). Meta تُرسل `GET` أولاً:
   - `hub.mode=subscribe`، `hub.verify_token`، `hub.challenge`؛ نردّ نص الـchallenge كما هو.
   - أي فشل يُكتب في `webhook_events` كـ`event_type=verification, status=rejected` مع السبب.
7. **الحقول في الشاشة**: Phone Number ID، WABA ID، Access Token (سري، يُخزَّن مشفّراً ولا يُعاد)، Verify
   Token (سري)، App Secret (سري). الشهادة بجانب البطاقة: `Live` / «جاهز» / «غير مُعد».
8. **اختبار الاتصال** يضرب `graph.facebook.com/debug_token` بالتوكن المحفوظ ويقول إن كان صالحاً ولمن.

اختبار سريع للرد الخارجي (بعد حفظ الأسرار، و`INTEGRATIONS_DRIVER=live`):

```bash
curl -sS -X POST "https://graph.facebook.com/v21.0/<PHONE_NUMBER_ID>/messages" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" -H "Content-Type: application/json" \
  -d '{"messaging_product":"whatsapp","to":"201012345678","type":"text","text":{"body":"اختبار من وسيط"}}'
# المتوقع: {"messages":[{"id":"wamid...", ...}]} — وهذا الـwamid هو ما يظهر في خيط الطلب كـexternal_id
```

ولاختبار الاستقبال من داخل النشر (بدون Meta) — جسم موقَّع بمفتاحك، ويجب أن يعطي 200 ويفتح طلباً:

```bash
SECRET="<APP_SECRET>"; BODY='{"object":"whatsapp_business_account","entry":[{"changes":[{"value":{"contacts":[{"wa_id":"201012345678","profile":{"name":"اختبار"}}],"messages":[{"id":"wamid.runbook_1","from":"201012345678","type":"text","text":{"body":"اختبار runbook"}}]}}]}]}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')"
curl -sS -X POST "https://<نطاق-الوسيط>/api/v1/webhooks/whatsapp" \
  -H "Content-Type: application/json" -H "X-Hub-Signature-256: $SIG" -d "$BODY"
# المتوقع: {"received":1,"results":[{"status":"created","request_id":"..."}]}
# إعادة نفس الأمر تعطي {"status":"duplicate"} — لا طلب ثانٍ ولا رسالة ثانية
```

### ب) مكالمات الموبايل (Mobile calls)

المقصود بالمكالمة هنا: **مكالمة هاتفية على رقم موبايل تملكه المنصة**، تصل من مزوّد مكالمات موبايل
(VoIP gateway) إلى webhook. لا يوجد في هذا الوصف «سنترال مكتبي أرضي»؛ المفهوم المنتَجي هو `مكالمات الموبايل`.

1. في لوحة المزوّد اضبط **Webhook URL** = `https://<نطاق-الوسيط>/api/v1/webhooks/mobile-calls`، ومفتاح
   التوقيع المشترك هو نفسه `webhook_secret` في الشاشة.
   المسار القديم `/api/v1/webhooks/telephony` ما زال يعمل كمرادف (حتى لا ينقطع مزوّد ضُبط سابقًا)، لكن
   الصق الجديد من الآن.
2. المزوّد الموثَّق: **Twilio Programmable Voice** — الحمولة `CallSid` / `CallStatus` / `From` / `To` /
   `CallDuration`، وحدث التسجيل `RecordingUrl` + `RecordingSid`. يُطبَّعها `parse_event` إلى
   `call.started` / `call.ended` / `recording_ready`، ويقبل أيضًا `uuid`/`status` (Vonage) و`callId`/`type`
   (بوابات عامة) والأسماء الصريحة (`call_id`, `event`, `from`).
3. الحقول في الشاشة: `provider` (`twilio` أو `generic`)، `provider_url` (جذر المزوّد، ويُستخدم في فحص
   الوصول **وفي حصر المضيف**)، `number` (رقم الموبايل الذي تستقبل عليه المنصة)، `trunk`، `webhook_secret`
   (سري). لا يوجد خيار `sim` في نوع السائق.
4. **مضيف التسجيل**: إن كان `provider_url` مضبوطاً فلن يُنزَّل تسجيل من مضيف آخر (422 + تحذير في السجل).
   إن كان المزوّد يقدّم التسجيلات من دومين CDN منفصل، أضف ذلك الدومين كـ`provider_url` — ولا تُفرغ الحقل.
5. **اختبار الاتصال** يتحقق من شيئين ويعرض السبب: توقيع HMAC متسق محلياً + هل `provider_url` يرد الآن
   (رفض 5xx = غير سليم). «التوقيع سليم لكن مزود مكالمات الموبايل غير قابل للوصول» رسالة مقصودة.

توقيع يدوي للاختبار:

```bash
SECRET="<WEBHOOK_SECRET>"; BODY='{"call_id":"CA-runbook-1","event":"call.started","from":"+201000000001","direction":"inbound"}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')"
curl -sS -X POST "https://<نطاق-الوسيط>/api/v1/webhooks/mobile-calls" \
  -H "Content-Type: application/json" -H "X-Call-Signature: $SIG" -d "$BODY"
# المتوقع: {"status":"started","request_id":"..."} — وطلب بقناة call يظهر للمدير/خدمة العملاء
```

`recording_ready` مع `"recording_url":"https://<host-المزوّد>/rec/x.wav"` يُنزّل الملف ويخزّنه بمفتاح
`calls/CA-runbook-1.wav`؛ وإن تعذّر التنزيل يظهر `recording_fetch_failed` في
`GET /api/v1/settings/integrations/events` ولا تُسقَط المكالمة.

### ج) البريد الوارد (R12، ADR-032) — ويبهوك موقّع، لا صندوق IMAP

لا يوجد «وضع حيّ» يُشغَّل: احفظ بيانات الاعتماد في الشاشة فتصبح القناة «متصل»، ودليل العمل أن
`آخر ويبهوك ناجح` يتحرك. البريد يدخل عبر مزوّد بريد وارد (SendGrid Inbound Parse / Mailgun Routes /
SES→SNS أو أي جسر) يرسل JSON إلى الـ Webhook URL الظاهر في كرت البريد:

```bash
SECRET=\"<INBOUND_SECRET>\"; BODY='{"external_id":"msg-1","from":"client@example.com","from_name":"عميل","subject":"تجديد وثيقة","body":"النص","attachments":[]}'
SIG=\"sha256=$(printf '%s' \"$BODY\" | openssl dgst -sha256 -hmac \"$SECRET\" -hex | sed 's/^.* //')\ncurl -sS -X POST \"https://<نطاق-الوسيط>/api/v1/webhooks/email\" \
  -H \"Content-Type: application/json\" -H \"X-Email-Signature: $SIG\" -d \"$BODY\"
# جديد: {"status":"created",...} · إعادة نفس external_id: {"status":"duplicate"} · مرسل معروف برسالة جديدة: {"status":"appended"}
```

- المرفقات `{filename, mime, content_base64}` تمرّ بقائمة MIME والحجم نفسها؛ نوع مرفوض = **422** لا إسقاط صامت.
- توقيع خاطئ/غائب (مع ضبط `inbound_secret`) = 401 ولا يُكتب أي صف.
- إعادة إرسال المزوّد بعد 5xx آمنة: `external_id` يمنع التكرار (مُثبت تحت الحمل: صفر مضاعفات).

### ب.١) قبل تشغيل الوضع الحيّ: ما يفحصه النظام، وما يقوله بالعربي (R7)

`live` لا يُشغَّل بمجرّد النقر: `GET /api/v1/settings/integrations/{provider}/preflight` يعيد **قائمة
الحواجز كاملة**، والشاشة تعطّل المفتاح وتكتب السبب بجانبه (ADR-027). الأسماء كما يراها المشغّل:

| المزوّد | الحاجز (`code`) | الرسالة كما تظهر |
|---|---|---|
| الثلاثة | `confirm` | «التبديل إلى الوضع الحيّ يحتاج تأكيدًا صريحًا (confirm=true)» |
| الثلاثة | `test` | «لم ينجح أي اختبار اتصال لهذا المزوّد بعد — شغّل «اختبار الاتصال» أولًا» |
| `whatsapp` | `fields` | «إعدادات ناقصة للوضع الحيّ: معرّف رقم واتساب (Phone Number ID)، توكين الوصول (Access Token)» |
| `telephony` | `fields` | «إعدادات ناقصة للوضع الحيّ: رابط مزود مكالمات الموبايل، سر توقيع أحداث مكالمات الموبايل (Webhook Secret)» |
| `email` | `fields` | «إعدادات ناقصة للوضع الحيّ: مضيف SMTP، بريد المرسل، كلمة مرور SMTP» |

(الحقول المطلوبة للوضع الحيّ تُقرأ من `PROVIDER_FIELDS` و`PRIMARY_SECRET` في
`app/services/integrations_settings.py` — الجدول أعلاه نقلٌ حرفي لرسائلها، لا اجتهاد.)

الترتيب العملي الذي يمرّ من أول مرة: **احفظ الحقول الناقصة → «اختبار الاتصال» حتى يصبح أخضر → المفتاح**.
الضغط على المفتاح قبل الاختبار يعطي `409` مقصوداً ومعه أسماء الحقول — لا خطأ صامت ولا شاشة مكسورة.

سطر واحد للتشخيص من الطرفية (بعد الحفظ):

```bash
TOKEN=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"manager@brokerhub.test","password":"Br0kerHub!2026"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/settings/integrations/whatsapp/preflight | python3 -m json.tool
# ready=false ⇒ لا تضغط المفتاح بعد؛ اقرأ blockers[].label وهي أسماء الحقول المطلوبة بالعربية
```

**ما لا يُدّعى:** لا يوجد في هذه الجولة حساب Meta ولا حساب مزوّد مكالمات موبايل حقيقي ولا رقم مرخّص،
فالاتصال الفعلي بمزود خارجي **لم يُجرَّب**. ما يلزم من المشغّل قبل أول مكالمة/رسالة حقيقية:
`WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` + `WHATSAPP_APP_SECRET` + `WHATSAPP_VERIFY_TOKEN`،
و`TELEPHONY_WEBHOOK_SECRET` + `TELEPHONY_WEBHOOK_URL` + رقم الموبايل. بلا هذه القيم في `ENV=prod` لا تقلع
العملية أصلًا (R9). المثبت أن مسار `live` كامل ومختبَر بعقد: توقيع HMAC على البايتات الخام، رفض التوقيع
الخاطئ، منع التكرار (idempotency)، وحصر مضيف التسجيل (SSRF) — `tests/test_live_channels.py` و
`scripts/live_matrix.py`، وإعادة تشغيل الوضع الحيّ من الشاشة مرتين لا تُنتج طلبين.

### ج) حسابات بوابة العملاء

- تُنشأ من **ملف العميل → «دخول بوابة العميل»**: اسم مستخدم (٣ أحرف على الأقل، `a-z0-9._-`) أو بريد.
- كلمة المرور المؤقتة تظهر **مرة واحدة**؛ إن ضاعت فاضغط «كلمة مرور جديدة» (يُبطل جلسات العميل القديمة).
- «إيقاف» يمنع الدخول (403) ويُبقي التاريخ؛ لا تحذف حساب عميل — أوقفه.
- سيناريو شكوى «العميل لا يستطيع الدخول»: تحقّق من `is_active`، ثم وجود `client_portal_users`، ثم أن
  اسم المستخدم لم يُغيَّر، وأخيراً `audit_logs` حيث `action='portal.password_reset'`.

## 6.2) المال: الدفتر، والصندوق، والعمولات (R10)

القاعدة الواحدة: **كل رقم مالي على أي شاشة هو مجموع صفوف `ledger_entries` بحالة `posted`**. لا يوجد عمود
رصيد مخزَّن يمكن أن ينحرف، ولا حذف لصف مُسجَّل.

### «العميل يقول إنه دفع، والقسط ما زال غير مدفوع»
1. `GET /api/v1/policies/{policy_id}/installments` — إن كان `status != paid` فالتحصيل لم يُسجَّل أصلًا:
   سجّله من شاشة `/policies` (زر «دفع») واختر طريقة الوصول (نقدي/بنكي/محفظة).
2. إن كان `paid` فهناك قيد `premium_receivable` مقابل: `GET /api/v1/finance/ledger?client_id=…` ويظهر
   `entry_no` في الرد نفسه.

### «سجّلنا إيصالًا بالخطأ» (لا حذف)
`POST /api/v1/finance/ledger/{entry_id}/void` بجسم `{"reason": "…"}` (صلاحية `finance.void`). الأثر:
`status=void` + `voided_at/by` + السبب + تدقيق `ledger.voided`، وكل الإجماليات **بما فيها رصيد الصندوق**
تنقص تلقائيًا لأن كل استعلام يفلتر `posted`. الإلغاء الثاني ⇒ 409.
الحالة التي لا تُعالج بالإلغاء: أن يكون القسط نفسه قد سُجّل مدفوعًا بالخطأ — لا يوجد «unpay» في R10
(سيُضاف كـ«إشعار دائن» في جولة تالية)؛ سجّل تسوية `adjustment` بالاتجاه المعاكس مع سبب مكتوب.

### «العمولة مدفوعة مرتين؟»
`GET /api/v1/finance/commissions?status=paid` — كل عمولة لها صف تسوية واحد (`commission_id` فريد في القاعدة)،
و`ledger_entry_no` يسمّي الحركة الخارجة. محاولة دفع ثانية ترجع 409 وتذكر `paid_at` و`ledger_entry_id`.

### «الصندوق لا يساوي ما في الدرج»
`GET /api/v1/finance/cashbox`: `balance` **نقدي فقط** (`method=cash`)، ثم `by_method` للتحويلات والمحافظ —
التحويل البنكي ليس في الدرج وقت الإقفال. أي فرق يعني إمّا حركة نقدية غير مسجَّلة، أو إيصالًا سُجّل بلا
طريقة (`method` فارغ لا يدخل الصندوق).

### من يرى ماذا
`finance.view` (لا يملكها المندوب ولا خدمة العملاء): الدفتر والصندوق والمستحقات والعمولات.
`finance.post` للكتابة، `finance.void` للإلغاء، `commissions.manage` للاعتماد/الصرف،
`reports.executive` للوحة الإدارة. تغيير صلاحية دور يتم من `/settings?tab=roles` ثم يلزم تسجيل دخول جديد
لأن الصلاحيات تُحمَّل في الجلسة.

## 7) حوادث متكررة — تشخيص من نقطتين

| العَرَض | أول فحص | ثاني فحص |
| --- | --- | --- |
| رسالة واتساب واردة لم تفتح طلبًا | `docker compose logs backend \| grep webhooks/whatsapp` (401 توقيع؟) | `GET /api/v1/settings/integrations/events` — صف `rejected` يحمل السبب؛ وتأكد أن App Secret نفسه في Meta وفي الشاشة |
| الرد على واتساب «تم» والعميل يقول لم يصله | `external_id` (wamid) في خيط الطلب: فارغ = لم تُرسَل | صفحة `/settings` → شهادة واتساب + «اختبار الاتصال» (توكن منتهٍ؟) |
| مكالمة مسجّلة بلا صوت | `webhook_events` حيث `event_type='recording_fetch_failed'` | مضيف `recording_url` ≠ `provider_url` (حصر SSRF) أو انتهت صلاحية الرابط قبل التنزيل |
| السياسة «صدرت» بلا ملف | `select * from policy_documents where policy_id='…'` | أعد الإصدار من مسودة (409 يعني أن الملف موجود) وتحقق من `STORAGE_DIR`/`STORAGE_BACKEND` |
| نفس الرسالة فتحت طلبين | `select external_id, count(*) from request_messages group by 1 having count(*)>1` | الفهرس الفريد على `external_id` موجود؟ (0002) |
| التسجيل لا يعمل | الشبكة: هل `src` يبدأ بـ `/api/v1/media/`؟ | صلاحية `calls.listen` للدور، وتاريخ `expires=` |
| المندوب يرى بيانات زميل | `Scope.mode` (يجب `owner`) | هل `account_owner_id/assigned_to` مضبوطة؟ وسياسات RLS مفعّلة (`ALTER TABLE … ENABLE ROW LEVEL SECURITY`) |
| بورتال أظهر بيانات داخلية | `grep -n "commission\|note" app/services/portal.py` (يجب ألا يوجد) | شغّل `pytest tests/test_portal.py -q` — فيه اختبار يمنع المفاتيح الحساسة |
| استيراد Excel معلّق | `select id,status,total_rows,stats from import_jobs order by created_at desc limit 5` | `status='dry_run'` يعني لم يُضغط تأكيد؛ `failed` اقرأ `error_report` |
| اللوحة أرقامها قديمة | TanStack Query cache: `staleTime` في `src/lib/api/client.ts` | شغّل `sla_sweep` يدويًا: `celery call broker_hub.sla_sweep` |
| 429 عند الدخول | `LOGIN_MAX_ATTEMPTS` (5) و`LOGIN_LOCKOUT_SECONDS` (300) | الجدول في `app/services/auth.py`؛ إعادة التشغيل تصفّر العدّادات في الوضع غير الدائم |

### ثلاث قواعد تعلّمناها بالدم (R5)

1. **`sqlite` ليس `postgres`.** العرض `VARCHAR(n)` لا يُفرض محلياً ويُفرض في الإنتاج. أي عمود يُكتب فيه
   ما يصل من مزوّد خارجي (حالة، نوع حدث، معرّف) يُختبر بعرضه لا بقيمته (ADR-024). المؤشر: تحقق من
   `INSERT` في `webhook_events` بعد أي حدث مكالمات موبايل.
2. **السجل لا يمنع العملية.** `record_event` يقصّ ولا يرفع؛ أثرٌ لا يُكتب أفضل من نداء ناجح يُلغى.
3. **قبل «تم»: الملف مفتوح.** أي PDF يُراجَع بصرياً (صفحة واحدة على الأقل) — تشكيل العربية لا يظهر في
   الاختبارات، ظهر في معاينة `pypdfium2` فقط.

## 8) النسخ الاحتياطي والاستعادة

```bash
docker compose -f infra/docker-compose.yml exec -T postgres \
  pg_dump -U broker -d brokerhub -Fc -f /tmp/brokerhub.dump
docker compose -f infra/docker-compose.yml cp postgres:/tmp/brokerhub.dump ./backups/
# الاستعادة (توقف الخدمات أولًا)
docker compose -f infra/docker-compose.yml exec -T postgres pg_restore -U broker -d brokerhub --clean --if-exists < ./backups/brokerhub.dump
```

- الوسائط: دلو MinIO (أو الـS3 المضبوط) — خُذ نسخة منه أيضًا؛ الطلبات بلا وسائط تكون ناقصة.
- لا تُخزَّن نسخ في المستودع؛ مجلد `backups/` خارج git.
- **المرفقات على تخزين محلي** (`STORAGE_BACKEND=local`): انسخ مجلد `STORAGE_DIR` كما هو بنفس الأسماء؛ مفاتيح
  الكائنات في قاعدة البيانات تشير لأسماء نسبية، فالاستعادة = dump + نسخ المجلد ثم تشغيل.
- **تدريب استعادة ربع سنوي**: استعد على قاعدة باسم آخر، شغّل `alembic upgrade head` ثم
  `GET /api/v1/ops/summary` (بحساب `settings.manage`) — «قاعدة البيانات: ok» + عدّ ملفات التخزين هو دليل النجاح،
  وسجّل التاريخ والنتيجة هنا يدويًا.

### تشغيل مالي شهري (Score-8، ADR-033)
- **الرصيد الافتتاحي**: من شاشة الصندوق (صلاحية `finance.post`) بعد جرد فعلي؛ بعده يقرأ الرصيد «افتتاحي ± الحركات»
  بدل تنويه «صافي حركات». تغييره لاحقًا = اعتماد جديد بتاريخ فعّال أحدث، وكل اعتماد مُدقَّق (`cashbox.opening_set`).
- **إقفال شهر**: من `/erp` (صلاحية `finance.period_close`). بعده أي ترحيل أو إبطال في الشهر المقفل مرفوض 409،
  إلا لحامل نفس الصلاحية ويُدقَّق كـ `ledger.posted_in_closed_period`. إعادة الفتح من نفس البطاقة ومُدقَّقة.
- **تصدير الفترة**: زر «تصدير Excel» في `/erp` — الأرقام من نفس تجميع الخادم الذي تقرأه الشاشة، وتُطابق مجموع الدفتر.

## 9) الطوارئ الأمنية

| حالة | تصرف |
| --- | --- |
| تسرّب `JWT_SECRET` | غيّره وأعد تشغيل الـAPI: كل access token يسقط فورًا. لا يمكن إبطال refresh دفعة واحدة بدون تدوير `refresh_token_version` — نفّذه لكل المستخدمين من SQL عند الحاجة |
| حساب مندوب مُسرَّب | عطّله من شاشة المستخدمين (`is_active=false`)؛ الدخول يُرفض والجلسة الحالية تسقط عند أول refresh |
| اشتباه قراءة تسجيلات بلا إذن | `select * from audit_logs where action='call.recording_listened' and occurred_at > now() - interval '1 day'` |
| تعديل صلاحيات خاطئ جماعي | `PUT /api/v1/roles/{id}/permissions` بالدور الصحيح؛ التدقيق في `audit_logs` يحمل `before/after` |
