# 014 — سلسلة الترقيات شُغِّلت لأول مرة على PostgreSQL: ما انكشف وما صُلح

- الحالة: مقبول (2026-10-26) · يُنقّح §«ما ليس مؤتمتاً» في ADR-003 ويُدخِل استثناءً على قاعدة ADR-001
- المرجع: §6 (العزل بشبكتين)، §13 (لا migration مُحرَّرة)، `docs/STATUS.md`

## السياق

قاعدة العمل في هذه الجولة: «ابدأ كل مرحلة بتشغيل الموجود وتسجيل الفشل، ثم أصلح، ثم أضف». أول تشغيل
فعليّ للمسارين الذين لا يراودهما شك — `alembic upgrade head` على PostgreSQL 16، والمجموعة كاملة على
PostgreSQL بدلاً من SQLite — أنتج الحالتين التاليتين:

```
$ DATABASE_URL=postgresql+asyncpg://…/brokerhub_rls alembic upgrade head
asyncpg.exceptions.UndefinedTableError: relation "users" does not exist
$ DATABASE_URL_TEST=postgresql+asyncpg://… python -m pytest -q
4 failed, 121 passed   —   function round(double precision, integer) does not exist
```

الأربع نقاط التي انكشفت ليست «أخطاء تجميل»، بل كلها في مسارات لا يراها إلا محرّك الإنتاج:

1. **0001 لا يعمل على PostgreSQL إطلاقاً.** جُمَل `op.create_table` الـ33 مرتّبة أبجدياً من مولّد
   alembic، فـ`clients` تُبنى قبل `users` التي تشير إليها بمفتاح أجنبي. النتيجة: `docker compose up`
   (المسار الموثّق للنشر، وخدمته الأولى `migrate`) لا تستطيع الإقلاع من قاعدة فارغة منذ اليوم الأول.
2. **`CREATE POLICY … FOR UPDATE, DELETE` ليست SQL صحيحة** — السياسة تسمّي أمرًا واحدًا. وهي مكتوبة
   هكذا في 0002 وفي المسودة الأولى من 0003، فـ0002 يُلغي الترقية بخطأ `syntax error at or near ","`.
3. **`pytest` لم يرَ PostgreSQL قطّ.** `ci.yml` يعرّف خدمة `postgres:16-alpine` ويطبع
   `DATABASE_URL_TEST`، لكن `tests/conftest.py` كان يبني محرّكه من ملف SQLite دائماً — فالخدمة كانت
   زينة، وعبارة ADR-003 «في CI تُشغَّل المجموعة كاملة فوق Postgres» كانت غير صحيحة، و«اختبارات
   `postgres_only` لم تُشغَّل» كانت تُقلّل من حجم الفجوة: لم يكن هناك اختبار `postgres_only` أصلاً.
   أول تشغيل على المحرّك الحقيقي وجد خطأً حقيقياً في الكود (`round(double precision, integer)` غير
   موجودة في PostgreSQL؛ التحديث في `services/exports.py` صار `cast(… → NUMERIC(12,4))` ثم `round`).
4. **هوية ترقيات أطول من العمود.** `alembic_version.version_num` هو `VARCHAR(32)` ولا يُوسَّع، و
   `0003_rls_portal_clause_and_app_role` (34 حرفاً) أفشل **آخر** خطوة من الترقية بعد تنفيذ الـDDL كله.

## القرار

1. **قاعدة الاختبار يحدّدها `DATABASE_URL_TEST`.** فارغة ⇒ SQLite (لا تهيئة للحلقة المحلية)،
   PostgreSQL ⇒ نفس المجموعة على محرّك الإنتاج (وهذا ما تفعله CI). محلياً شُغِّلت بـحزمة `pgserver`
   (PostgreSQL 16 مضمّن، بدون Docker). علامة `postgres_only` صارت مستعملة فعلاً، ومن لا PG لديه
   يُتخطّى بصرخة لا بصمت.
2. **0001 و0002 حُرِّرا، استثناءً صريحاً من قاعدة «لا تُحرَّر migration مُدمجة».** المبرير:
   كلاهما غير قابل للتطبيق على محرّك الإنتاج الوحيد، ولم تُطبَّق أيّهما على أي قاعدة، فليس هناك حالة
   يجب ترحيلها؛ والبديل (ترقية جديدة تُصحّح ما قبلها) مستحيل لأن 0001 يفشل قبل أن تُقرأ أي ترقية بعده.
   حتى لا تتحول الاستثناءات إلى عادة، مُرِّر القيد عبر البوابة نفسها:
   `scripts/check_migrations.py` يرفض تعديل مدمج إلا إذا كان ملف الترقية نفسها يحمل
   `# ALEMBIC-REWRITE-APPROVED: <سبب>` — أي أن الاستثناء يظهر في نفس الـdiff الذي يأخذه.
   (وبهذه المناسبة: البوابة كانت موثّقة في ADR-001 على أنها «في CI» لكنها لم تكن موصولة بأي وظيفة؛
   صارت تُشغَّل في وظيفة `backend`، مع `fetch-depth: 0` لأن `origin/main` لازمها.)
3. **الإصلاح في 0001 ترتيبٌ لا دلالة.** إعادة ترتيب الجُمَل ترتيباً طوبولوجياً حسب `ForeignKeyConstraint`
   الداخلية (خوارزمية مستقرة: ما ليس له تبعيات يحافظ على ترتيبه الأبجدي)، وكسر الحلقة `leads ↔ clients`
   بإخراج ضلع `clients.source_lead_id` من الـ`CREATE TABLE` وإضافته بعد وجود الجدولين في وضع batch
   (`op.batch_alter_table`) — `ALTER` صريح على PostgreSQL وإعادة بناء الجدول على SQLite. لا عمود أُضيف
   ولا حذف ولا أُعيدت تسميته.
4. **السياسة الواحدة `FOR ALL` لكل جدول في 0003**، بنسختها من نفس المحدِّد في `USING` و`WITH CHECK`.
   السبب ليس الأناقة: سياسة قراءة بلا سياسة كتابة على جدول مجبَر (`FORCE`) تعني أن PostgreSQL **يرفض كل
   `INSERT`** للدور غير المميّز — أي كان الممكن تشغيل الشبكة على 0002 لفوراً كانت إنشاءات Leads كلها
   ستسقط. مع ذلك: `WITH CHECK` لـ`UPDATE` فقط عند الحاجة، و`FOR ALL` تغطي الأوامر الأربعة.
5. **شبكة RLS لها دور تُطبَّق عليه، أو لا معنى لها.** `FORCE ROW LEVEL SECURITY` لا يسري على superuser،
   وكل البيئات كانت تتصل كمالك القاعدة. 0003 ينشئ `brokerhub_app` (`NOLOGIN NOBYPASSRLS`، منح بيانات
   فقط، لا `TRUNCATE`/`REFERENCES`)؛ وإنشاؤه مشروط بصلاحية `CREATEROLE` حتى لا تُجهض الترقية في بيئة
   تملك السياسات ولا تملك الأدوار.
6. **الوصل عبر خطّاف `after_begin` لا عبر `SET` لكل طلب** (`app/core/rls.py`): نقاط النهاية تعمل `commit`
   في منتصف الطلب، فتضيع قيم `SET LOCAL`؛ الخطّاف يعيد `set_config(..., true)` عند كل معاملة جديدة،
   وتنتهي القيم تلقائياً عند حدّ المعاملة فلا تسرّب على اتصال الـpool. لم يُعَدّل `DbSession` ليصبح
   `ScopedSession` (اسم الجلسات الموجَّهة للطلبات فقط) لأن الاعتمادية الدائرية ترفضها FastAPI:
   `scoped_session → ScopeDep → CurrentUser → DbSession`.
7. **أسماء الـGUC صارت ثابتاً واحداً** (`rls.Guc`) بدل نصوص مكرّرة في الوصل والـDDL والاختبارات،
   و**DDL السياسات انتقل إلى `app/core/rls_ddl.py`**: 0003 ينفّذه، واختبارات `postgres_only` تنفّذه على
   قاعدة `create_all` — أي أن المُختبَر هو المنقول إلى الإنتاج، لا نسخة منه.
8. **بوّابتان ضدّ هذا الصنف كاملاً، لا إصلاح واحد**: `tests/test_migrations.py` يبني قاعدة SQLite مؤقتة
   ويشغّل `upgrade head → downgrade base → upgrade head`، ويقارن المخطط الناتج بنماذج `Base.metadata`
   (جداول/أعمدة/فهارس)، ويرفض هوية أطول من 32 حرفاً، ويشغّل السلسلة على قاعدة PostgreSQL فارغة
   (تُنشأ وتُهدم) ثم يتحقق من السياسات و`FORCE` ومن الدور. ووظيفة `backend` في CI فيها خطوة صريحة
   «Migration chain on a fresh PostgreSQL database» لأن فشل هذه الخطوة يعني أن `compose up` مكسور،
   وهذا يجب أن يُقرأ كذلك لا كسقوط اختبار.

## الدليل (نفس الأوامر، مخرجات حقيقية)

```
$ alembic upgrade head        (قاعدة PostgreSQL 16.2 فارغة)  → rc 0
$ alembic downgrade base                                       → rc 0
$ alembic upgrade head                                         → rc 0
   33 جدولاً · 55 مفتاحاً أجنبياً · السياسات: ['clients_scope', 'leads_scope']
   pg_class: clients(relrowsecurity=t, relforcerowsecurity=t) · leads(نفس)
   pg_roles: brokerhub_app (bypass=false, login=false)
$ alembic upgrade head   (ملف SQLite فارغ)                      → rc 0 · 41 فهرساً · قيد leads موجود
$ python -m pytest                                  (SQLite)     → 129 passed, 9 skipped in 97.91s
$ DATABASE_URL_TEST=postgresql+… python -m pytest   (PG 16.2)   → 138 passed in 113.92s
$ … python -m pytest tests/test_rls_postgres.py tests/test_migrations.py → 13 passed in 10.08s
$ ruff check . · ruff format --check . · mypy app                → نظيف (96 ملفاً)
$ coverage report                                                → TOTAL 5601/979 → 82.5%
```

## الأثر

- النشر بالـcompose صار ممكناً من قاعدة فارغة؛ ومعه أول تشغيل لوظيفة `migrate` صار منطقياً أن يُطلب في CI.
- الاختبارات على SQLite ما زالت مفيدة للحلقة السريعة، لكنها لم تعد تُسمَّى «تغطية PostgreSQL».
- الأمان الفعلي في الإنتاج يتطلب تغيير `DATABASE_URL` إلى `brokerhub_app` (خطوتان في runbook §5) —
  الترقية تنشئ الدور ولا تغيّر سلسلة الاتصال. حتى يحدث ذلك، الشبكة الثانية تعمل لمن يلمس القاعدة مباشرة.
- قاعدة «لا تُحرَّر migration مُدمجة» صارت مكتوبة في أداتها: تعديل بلا وسم `ALEMBIC-REWRITE-APPROVED` يفشل CI.
