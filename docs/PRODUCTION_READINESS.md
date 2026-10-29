# جاهزية الإنتاج — Mantiq

هذا الملف هو الجواب الوحيد على سؤال «هل المنصّة جاهزة لـLive Production؟». كل بند فيه إما **مُنفَّذ
بدليل أمر**، أو **مُتحقَّق منه باختبار**، أو **مفتوح بسبب مكتوب** — ولا يوجد بند رابع.

آخر تحديث: 2026-10-29 · الفرع `arena/edf6520b-mantiq-platform`.

---

## 1) جدول الحالة (سبع مشكلات التسليم)

| # | المشكلة | الحالة | الدليل |
| --- | --- | --- | --- |
| 1 | `backend/.env` داخل الريبو | **أُصلح في الشجرة** — حُذف، و`.gitignore` يمنع أي `.env`. **يبقى في تاريخ Git** (إجراء أدناه). | `git ls-files \| grep .env` → ثلاث ملفات `.example` فقط |
| 2 | لا `README.md` في الجذر | **أُصلح** | [`README.md`](../README.md) |
| 3 | بقايا أسماء قديمة | **قُتلت الظاهرة للعميل، والباقي مُوثَّق بقرار** (§3) | `tests/test_brand_guard.py` (10 اختبارات) |
| 4 | حرّاس بدء الإنتاج | **أُصلح ووُسِّع** — 15 قاعدة، كلها في اختبارات | `tests/test_production_boot_guards.py` (23) |
| 5 | لا compose إنتاج واضح | **أُضيف** + مُدقِّق آلي | `infra/docker-compose.prod.yml` + `infra/validate_prod_stack.py` |
| 6 | `.env.example` ناقص | **أُكمل** (مفاتيح، وتعارض اسم القاعدة أُصلح) | `backend/.env.example` · `infra/.env.prod.example` |
| 7 | `SEED_PASSWORD` يعمل في الإنتاج | **أُصلح** — البذرة ترفض `ENV=prod` | `tests/test_production_boot_guards.py::test_the_seeder_refuses_to_run_in_production` |

---

## 2) المشكلة 1 — `backend/.env`

**ما نُفِّذ**

- `git rm --cached backend/.env` + حذف الملف من الشجرة.
- `.gitignore` يحمي: `.env`, `.env.*`, `*.pem`, `*.key`, `secrets/` — مع استثناء `.env.example`.
  تحقّق: `git check-ignore -v backend/.env` → نعم؛ `git check-ignore backend/.env.example` → لا.

**ما كانت تحتويه القيم (مهم للتقييم)**

كان نسخة من `.env.example` تقريبًا: `DATABASE_URL` بكلمة `broker123`، و`JWT_SECRET` طوله 34 حرفًا (قيمة
تطوير)، و`S3_ACCESS_KEY=minioadmin`. **لا سرّ إنتاجي تسرّب** — لكن الملف نفسه كان يجب ألا يوجد.

**ما تبقّى: تاريخ Git**

الملف موجود في الكوميتات السابقة. التنظيف الكامل يحتاج **إعادة كتابة التاريخ + force-push على `main`**،
وهذا لم يُنفَّذ من هذه الجلسة لسببين حقيقيين لا لأنّه غير مهم:

1. النسخة هنا **shallow clone** (`git rev-parse --is-shallow-repository` → `true`، `rev-list --count` = 1)،
   فلا يملك التاريخ كاملًا ليُعيد كتابته.
2. صلاحية الدفع في هذه الجلسة **مقصورة على الفرع الحالي**؛ الدفع القسري إلى `main` خارجها.

**الإجراء الذي ينفّذه مالك الريبو** (مرة واحدة، على نسخة كاملة، وينسّق مع كل من لديه نسخة):

```bash
# 1) نسخة احتياطية
git clone --mirror https://github.com/DevMagdi/Mantiq_Platform.git mantiq-backup.git
# 2) إزالة الملف من كل التاريخ
pip install git-filter-repo
git clone https://github.com/DevMagdi/Mantiq_Platform.git mantiq-clean && cd mantiq-clean
git filter-repo --path backend/.env --invert-paths
# 3) دفع قسري + إبلاغ الفريق (كل نسخة محلية يجب أن تُعاد نسخها)
git push --force origin --all && git push --force origin --tags
```

وبعدها: **دوّر أي سرّ كان في الملف على أي بيئة استُخدم فيها** (كلمات مرور القواعد، `JWT_SECRET` الذي
شاركته بيئة تطوير مشتركة، مفاتيح S3). القيم هنا تطويرية، فالتدوير وقائي لا إلزامي.

> إجراء احترازي أُضيف الآن: `JWT_SECRET` المطابق للنشر في الريبو (`dev-only-insecure-secret-change-me`)
> صار **مرفوضًا صراحةً** في الإنتاج ولو كان طوله 34 حرفًا — كان يمرّ من فحص الطول فقط.

---

## 3) المشكلة 3 — الأسماء القديمة: القرار

القاعدة المتّبعة: **ما يقرأه العميل يُنظَّف، وما هو معرّف داخلي يُوثَّق.** الجدول هو العقد:

| الاسم | أين | يظهر للعميل؟ | القرار |
| --- | --- | --- | --- |
| `broker-hub-*.xlsx` | اسم ملف التصدير | **نعم** (مجلد التنزيلات) | **غُيِّر** إلى `mantiq-*.xlsx` |
| «Broker Hub» في الواجهة/العنوان/PDF/البريد | كل السطوح | نعم | **نظيف** من R13 ويحرسه اختبار |
| `@broker-hub/frontend` | `package.json` | لا (حزمة خاصة غير منشورة) | **غُيِّر** إلى `@mantiq/frontend` — بلا أي مخاطرة تشغيلية |
| `brokerhub` (قاعدة البيانات) | compose + `DATABASE_URL` | لا | **باقٍ** — إعادة تسميته هجرة بنافذة قفل |
| `brokerhub_app` / `brokerhub_load` | أدوار PostgreSQL | لا | **باقٍ** — RLS مبنيّ عليها |
| `broker_hub` (Celery app + أسماء المهام) | رسائل الطابور | لا | **باقٍ** — إعادة التسمية تُهمل ما في الطابور |
| `broker_hub_*` (Prometheus) | `/metrics` | لا (SRE فقط) | **باقٍ** — لوحات وتنبيهات تُبنى على الاسم |
| `bh.lang` | localStorage | لا | **باقٍ** — إعادة التسمية تُفقد تفضيل اللغة |
| `brokerhub` (مخزن S3 الافتراضي) | قيمة افتراضية | لا | **باقٍ** — كل نشر يضبط مخزنه |
| `broker-hub` (اسم مشروع/صور dev compose) | `infra/docker-compose.yml` | لا | **باقٍ** — تغييره يُنشئ وحدات تخزين فارغة؛ و**ملف الإنتاج الجديد يستخدم `mantiq`** |

**الحارس:**

- `backend/tests/test_brand_guard.py` يفشل إذا ظهر الاسم القديم في أي سطح يقرأه العميل (PDF، XLSX،
  بريد، لوحة المحاكي، بيانات OpenAPI)، ويفشل أيضًا إذا بقي اسم في جدول «الداخلي» لم يعد موجودًا — فتبقى
  القائمة صادقة لا أرشيفًا.
- الواجهة: `frontend/tests/tokens.test.ts` و`e2e/brand.spec.ts` (من R13) يحرسان العنوان والشعار.

---

## 4) المشكلة 4 — حرّاس بدء الإنتاج

كل قاعدة **توقف العملية** عند `ENV=prod`، وتُجمَّع كل المخالفات في **خطأ واحد** حتى يُصلح المشغّل ملفًا
واحدًا لا أن يعيد التشغيل مرة لكل خطأ.

| # | القاعدة | لماذا |
| --- | --- | --- |
| 1 | `ENV=production` ≡ `prod` | اسمان لحالة واحدة |
| 2 | `INTEGRATIONS_DRIVER=sim` مرفوض صراحةً؛ **غير المضبوط يصبح `live`** | كان القبول يعتمد على القيمة الافتراضية `sim`، فنصيحة الخطأ نفسها («احذف المتغيّر») كانت تُفشل الإقلاع |
| 3 | `JWT_SECRET` ≥ 32 **وليس** القيمة المنشورة | القيمة المنشورة 34 حرفًا، ففحص الطول وحده كان يمرّرها |
| 4 | `DEBUG=false` | tracebacks وSQL للعميل |
| 5 | `COOKIE_SECURE=true` | كوكي التحديث على HTTPS فقط |
| 6 | `CORS_ORIGINS` غير فارغة، بلا `*`، بلا localhost، وبـ`https` فقط | API أي-أصل مع كوكي |
| 7 | `SECRETS_ENCRYPTION_KEY` موجود **عند الإقلاع** | كان يُفحص عند أول استخدام للشاشة، أي بعد الإقلاع |
| 8 | `DATABASE_URL` على PostgreSQL | SQLite: لا RLS وكاتب واحد |
| 9 | `STORAGE_BACKEND=s3` (بمفاتيحه) أو `local` في مسار **غير مؤقّت** | الافتراضي `/tmp/…` يضيّع المرفقات عند إعادة التشغيل |
| 10 | `CELERY_TASK_ALWAYS_EAGER=false` | تنفيذ المهمة داخل الطلب يحجبه (PDF/تصدير/مسح SLA) |
| 11 | أسرار dev المنشورة تُفرَّغ | `dev-app-secret` مكتوب في الريبو |
| 12 | `python -m app.seed` مرفوض | حسابات العرض بكلمة مرور منشورة |

**الاختبارات:** `backend/tests/test_production_boot_guards.py` — 23 اختبارًا: حالة تحكّم (إعداد إنتاج
كامل يُقلع)، واختبار لكل قاعدة تعطّل قيمة واحدة فقط، واختبار أنّ كل المخالفات تُذكر معًا، واختبار أنّ
بيئة التطوير لم تتأثر (وإلا لن يستطيع أحد التطوير محليًا).

**تحقّق إضافي:** كتلة البيئة في `docker-compose.prod.yml` بالكامل (46 متغيّرًا) بحشو واقعي → `Settings`
تُقلع: `ENV=prod driver=live debug=False cookie_secure=True cors=['https://app.example.com']`.

---

## 5) المشكلة 5 — مسار إنتاج واحد واضح

```bash
cp infra/.env.prod.example infra/.env.prod && chmod 600 infra/.env.prod
docker compose -f infra/docker-compose.prod.yml --env-file infra/.env.prod up -d
```

`infra/docker-compose.prod.yml` — **7 خدمات**: `postgres` · `redis` · `migrate` (لمرة واحدة) ·
`backend` · `worker` · `beat` · `web`.

| خاصية | التفصيل |
| --- | --- |
| الشبكة | `postgres`/`redis`/`backend`/`worker`/`beat` **بلا منافذ مضيفة** — nginx وحده يكلّمها |
| الصحة | `readiness` للـAPI (`/readyz` يلمس القاعدة)، `pg_isready`، `redis-cli ping`، `celery inspect ping`، و`beat` بفحص عملية |
| التشغيل | `restart: unless-stopped` على كل خدمة دائمة |
| الموارد | حدود CPU/ذاكرة لكل خدمة، وسقف لذاكرة Redis بـ`noeviction` (طابور لا يُسقط مهامًا بصمت) |
| السجلات | `json-file` بدوران (20m × 5) — سجل بلا سقف يملأ القرص ويعطّل الخدمة |
| TLS | nginx 443 + إعادة توجيه 80، HSTS، رؤوس أمان، شهادات من وحدة قراءة فقط |
| SSE | `/api/v1/realtime/stream` بلا تخزين مؤقت ومهلة قراءة ساعة — وإلا وصل البثّ متأخرًا |
| البيانات | وحدات `pgdata` / `redisdata` / `objectsdata` |

**التحقق بدون Docker** (لا يوجد Docker في بيئة التحقق): `python infra/validate_prod_stack.py` →
`✓ 7 services, 46 variables, all documented`. يفحص: صلاحية YAML، وجود كل `*anchor`، أن كل `${VAR}` موثّق
في `.env.prod.example`، أن كل `${VAR:?}` مطلوب موجود، وأن كل خدمة لها restart/logging/healthcheck وأن
الخدمات الخاصة لا تنشر منافذ.

> **لم يُشغَّل:** `docker compose config` والـ`up` الفعلي، لعدم وجود Docker daemon في هذه البيئة.

---

## 6) المشكلة 6 — `.env.example`

| ما أُضيف/أُصلح | السبب |
| --- | --- |
| `APP_NAME`, `TRUST_HOPS` | كانا في الكود بلا توثيق؛ الثاني يؤثر على تسجيل IP الحقيقي خلف وكيل |
| `DATABASE_URL` → `brokerhub` | كان `broker_hub` ويخالف `POSTGRES_DB=brokerhub` في compose |
| تنبيه «لا تُكمل `.env`» + قاعدة `SEED_PASSWORD` | منع تكرار المشكلة 1 |
| `infra/.env.prod.example` (جديد) | كل مفتاح إنتاج مطلوب، بلا قيمة افتراضية صالحة |

`JWT_ALGORITHM` و`API_V1_PREFIX` **بقيا غير موثّقين عمدًا**: الأول ليس مقبضًا للمشغّل (تغييره لكسر
التوقيع)، والثاني ثابت داخلي.

---

## 7) المشكلة 7 — البذرة

`app/seed.py`: `assert_seed_allowed()` تُقرأ في `main()` وفي `seed()` نفسها، فحتى الاستيراد البرمجي
يرفض. الرسالة تشرح البديل (إنشاء أول مدير عبر الخدمة). `ENV` غير المضبوط = تطوير.

---

## 8) RLS على PostgreSQL — مُفعَّل ومُختبر (لا SQLite)

شُغِّل PostgreSQL 16.2 حقيقي (pgserver) في هذه الجلسة، بُني المخطّط بـ11 هجرة، ثم:

```
leads   | rls_on = t | forced = t | policy: leads_scope   (FOR ALL)
clients | rls_on = t | forced = t | policy: clients_scope (FOR ALL)
brokerhub_app: rolbypassrls = f, rolsuper = f
```

**إثبات العزل** بدور التطبيق (ليس superuser) بنفس GUCs التي تنشرها `Scope.session_settings`:

| النطاق | `leads` المرئية | منها لمستخدم آخر |
| --- | --- | --- |
| مالك (مندوب واحد، `bypass_rls=off`) | **16** من 63 | **0** |
| مدير (`bypass_rls=on`) | 63 | — |
| بلا نطاق (مهام نظام/هجرات) | 63 | — |

و`clients` بنفس نطاق المندوب: صفّان فقط. **اختبارات المستودع نفسها:** `pytest tests/test_rls_postgres.py`
→ **8/8 نجحت** على PostgreSQL (تُتخطّى على SQLite).

**ملاحظة صادقة من التحقيق:** محاولة أولى بضبط `scope_user_id` وحدها أعادت 63 صفًّا. السبب ليس عيبًا:
السياسة تعتبر «`bypass_rls` غير مضبوط» = وصول نظام، وهذا مقصود (مهام الخلفية والهجرات). التطبيق يضبط
القيمتين معًا دائمًا (`core/scope.py`)، وبعد ضبطهما معًا ظهر العزل الصحيح.

---

## 9) التحقق الكامل (أوامر ومخرجات)

| الأمر | النتيجة |
| --- | --- |
| `pytest -q` (SQLite) | 359 مُجمَّعة · **350 نجحت · 9 تخطٍّ** (المتخطّاة هي اختبارات Postgres تحديدًا) |
| `pytest -q` على **PostgreSQL 16.2** | **359 نجحت · 0 فشل · 0 تخطٍّ** (exit 0) |
| `tests/test_rls_postgres.py` وحده | 8/8 |
| `ruff check .` · `mypy app` | نظيف · نظيف |
| `check_openapi_fresh.py` | fresh (129 مسارًا) |
| `npm run codegen:check` | `schema.d.ts is in sync with openapi.json` |
| `npm run lint` · `typecheck` · `vitest` · `build` | نظيف · نظيف · 20 ملفًا/135 · ✓ |
| `python infra/validate_prod_stack.py` | ✓ 7 خدمات · 46 متغيّرًا · كلها موثّقة |
| حمل حقيقي (PostgreSQL) | **[تقرير كامل](perf/r13-load-2026-10-29.md)** — 22,015 طلبًا · **0 فشل** · 0 5xx |

---

## 10) ما لا يزال مطلوبًا من المالك (قرارات، لا كود)

1. **إعادة كتابة التاريخ** لإزالة `backend/.env` (§2) — يحتاج force-push على `main`.
2. **أسرار الإنتاج الحقيقية**: `JWT_SECRET` (`openssl rand -hex 32`)، `SECRETS_ENCRYPTION_KEY` (Fernet)،
   كلمة مرور Postgres. الحُرّاس يرفضون أي قيمة افتراضية.
3. **اعتمادات Meta / PBX الحقيقية** ثم `INTEGRATIONS_DRIVER=live` من شاشة `/settings` — بدونها تبقى
   القنوات غير جاهزة، و`assert_production_ready()` يمنع الإقلاع بدل أن يعمل على محاكي.
4. **شهادات TLS** في `TLS_CERT_DIR` (Let's Encrypt أو غيره) قبل `up`.
5. **S3 حقيقي** أو وحدة تخزين دائمة (`STORAGE_DIR` غير `/tmp`).
6. **Relay بريد حقيقي** (`SMTP_URL` + `EMAIL_FROM` موثّق) — بدونه لا إشعارات ولا تذكيرات.
7. **نسخ احتياطي واستعادة**: `pg_dump` مجدول + اختبار استعادة. **غير منفّذ** ولا يوجد سكربت له في الريبو.
8. **مراقبة**: `/metrics` موجود، لكن لا Grafana/Prometheus في الريبو. `METRICS_TOKEN` إلزامي عمليًا.
9. **تشغيل `docker compose config` + `up` فعلي** على جهاز فيه Docker، وتجربة `web` بـTLS حقيقي.
10. **Postgres ≥ 13 على نسخة الإنتاج**: كل الاختبارات هنا على 16.2.

---

## 11) ملاحظات بيئة التحقق

- Python هنا **3.11.2** بينما المشروع يعلن `requires-python >= 3.12` وصور/CI تستخدم 3.12. كل الاختبارات
  أعلاه جرت على 3.11 — أي أن 3.12 لم تُختبر في هذه الجلسة.
- لا Docker، لا Redis، لا S3، لا ffmpeg. كل ما يعتمد عليها مكتوب أعلاه كـ«لم يُشغَّل» مع السبب.
