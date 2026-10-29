# 003 — SQLite في اختبارات الوحدة، وPostgreSQL في اختبار التكامل

**الحالة:** مقبول · **التاريخ:** 2026-10-26

## السياق
المواصفة تفرض PostgreSQL 16 مع Row-Level Security، لكن تشغيل Postgres في كل اختبار وحدة يجعل
الحلقةdevelopment بطيئة ولا يُضيف تغطية لمنطق الخدمات. في بيئة التطوير هنا لا يوجد Docker ولا
Postgres أصلاً.

## القرار
- اختبارات الخدمات/النطاق/العزل تعمل على `sqlite+aiosqlite:///` بملف مؤقت لكل جلسة، مع
  `PRAGMA foreign_keys=ON` من `app/core/db.py` (SQLite لا يفعّل المفاتيح الأجنبية افتراضياً،
  فبدونه تمر أخطاء سلامة لا يرفضها Postgres).
- العلامة `postgres_only` معرّفة في `pyproject.toml` لكل اختبار يعتمد على سلوك Postgres الحصري
  (RLS، الفهارس الجزئية، `FORCE ROW LEVEL SECURITY`)، ولا يُشغَّل منها شيء في بيئة التطوير هنا.
- في CI تُشغَّل المجموعة كاملة فوق Postgres+Redis خدمات، لذلك لا يوجد اختبار «مريح» لـ SQLite فقط.

## ما ليس مؤتمتاً (مهم)
`0002_pg_rls_and_partial_indexes.py` يفعّل RLS على `leads`/`clients` ويكتب سياسيات تقرأ
`app.scope_user_id` و`app.bypass_rls`، و`Scope.session_settings()` في `app/core/scope.py` يُنتج
تلك القيم… **لكن لا يوجد استدعاء يطبّقها على جلسة الطلب**، فـ GUC تبقى غير مضبوطة والسياسيات
تسمح ضمناً. الشبكة الثانية لذلك حالياً: طبقة المستودعات (مغطاة بـ `tests/test_isolation_matrix.py`
و`tests/test_scope_matrix.py`) + فحص يدوي بالـ SQL في `docs/runbook.md` §5. ربط الجلسات بالـ GUC
مطلوب قبل أي اعتماد على RLS في الإنتاج، ومعه اختبار `postgres_only` حقيقي — التسبيب موثّق في
`docs/STATUS.md`.


## الأثر
`pytest -q` محلياً أخضر بلا بنية تحتية، لكن «أخضر محلياً» لا يعني اختبار RLS — لذلك تفصل
`docs/STATUS.md` صراحةً بين ما شُغِّل وما لم يُشغَّل.

## تحيين 2026-10-26 — الفقرة السابقة صارت منتهية (انظر ADR-014)

- «لا يوجد استدعاء يطبّق الـGUC على جلسة الطلب» لم تعد صحيحة: `app/core/rls.py` يثبّتها عند كل
  `after_begin` لجلسة الطلب (`DbSession`)، فلا تضيع بـ`commit` نصفي ولا تسرّب على الاتصال التالي.
- «في CI تُشغَّل المجموعة كاملة فوق Postgres» كانت غير صحيحة عملياً (`conftest.py` كان يبني SQLite دائماً)
  وصارت صحيحة: قاعدة الاختبار يحدّدها `DATABASE_URL_TEST`، والمجموعة خضراء على PostgreSQL 16 حقيقي
  (138) كما على SQLite (129 + 9 مُتخطَّاة بعلامة `postgres_only`).
- بقيت العبارة الوحيدة التي تصح للأبد: أخضر SQLite لا يعني اختبار RLS — ولم يعد ذلك مبرراً لتفاديها،
  لأن `tests/test_rls_postgres.py` و`tests/test_migrations.py` يشغّلانها.
