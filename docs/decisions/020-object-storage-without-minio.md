# 020 — وسائط المكدّس المحلي: لا MinIO (صورٌ محذوفة من السجلّات)، ومخزن على وحدة `objectsdata`

- الحالة: مقبول (2026-10-27) · جولة R3 (إصلاح وظيفة `docker` في CI)
- المرجع: `infra/docker-compose.yml`، `infra/backend.Dockerfile`، `backend/app/integrations/storage.py`،
  `.github/workflows/ci.yml` (وظيفة `docker`)، ADR-011، ADR-016

## السياق: الدليل من CI نفسه، لا تخميناً

وظيفة `docker` كانت حمراء على `main` ثلاث جولات، وسجلّها غير قابل للقراءة من بيئة المراجعة (يُخدَم من
`*.blob.core.windows.net`). في الجولة الأولى أضفنا خطوة تنشر تشخيصها على الـPR، فأظهرت الجولة الأولى
سبباً حقيقياً (`entrypoint` نصّي بـ`sh -c` مرفوض)، وبعد إصلاحه أظهرت الجولة الثانية:

```
 Image minio/minio:latest Error pull access denied for minio/minio,
       repository does not exist or may require 'docker login'
Error response from daemon: pull access denied for minio/minio
 (وبعدها: postgres/redis/mailpit/minio-mc كلها Interrupted)
```

أي أن الفشل **قبل** إنشاء أي حاوية (`ps -a` كان فارغاً) وقبل الترحيل: لا علاقة له بـ`healthcheck` ولا
بـ`entrypoint`. الصورة نفسها لم تعد موجودة.

هذا ليس عارضاً خاصاً بهذه المستودع: MinIO أرشفت مستودعها (2026-04-25)، ثم **حذفت** `minio/minio`
و`minio/mc` من Docker Hub (2026-09-11)، ثم بدأت `quay.io` ترفض السحب المجهول (2026-09-24) — ولذلك لم
يكن الحل «حوّل إلى quay.io»: كان سيفشل في التشغيل التالي بنفس الخطأ بصيغة `unauthorized`.

## القرار

1. **لا خدمة MinIO في مكدّس اللابتوب/CI.** حُذفت `minio` و`minio-init` من `infra/docker-compose.yml`.
2. **المخزن الافتراضي للمكدّس صار القرص** (`STORAGE_BACKEND=local`، `STORAGE_DIR=/srv/objects`) على وحدة
   مسماة `objectsdata` تُوصَل بـ`migrate` و`backend` و`worker` و`beat` — نفس الدليل، لأن `workers/tasks/media.py`
   ينزل الملفات المؤقتة تحته قبل الضغط.
3. **S3 يبقى مواطناً أولاً في المنتج**: `S3Storage` كما هو (`storage()` يختاره بـ`STORAGE_BACKEND=s3`)،
   ويُنشئ الحاوية عند أول استخدام (`head_bucket` ثم `create_bucket`) — لذلك لم تكن `minio-init` لازمة أصلاً.
   النشر الذي يشغّل S3/MinIO خاصاً به يضبط `STORAGE_BACKEND=s3` + `S3_ENDPOINT_URL` + `S3_BUCKET` + المفاتيح،
   وهذا مذكور في `infra/README.md` و`docs/runbook.md`.
4. **المجلد يُنشأ داخل الصورة وبمالكه الصحيح**: `RUN mkdir -p /srv/objects` قبل
   `chown -R broker:broker /srv`. وحدة Docker تُهيّأ بملكية المسار الموجود في الصورة؛ لو لم يوجد لكانت
   `root:root`، ولفشل كل رفع مرفق بخطأ صلاحيات لا يشبه سببه (uid 10001).
5. **وظيفة `docker` صارت تنشر سبب فشلها كاملاً**: `compose up` يُكتب إلى `/tmp/compose-up.log` عبر
   `tee` + `pipefail`، والتشخيص يعيد نشره مع `ps -a` وسجلّات الحاويات وقائمة الصور المطلوبة وحالتها.

## ما لم يُفعل (ولماذا)

- **لم يُحوَّل إلى `quay.io/minio/*`**: مصدر عام واحد على الأقل يوثّق أن Quay صارت ترفض السحب المجهول منذ
  2026-09-24؛ وتحويل السجل مع بقاء نفس الاعتماد كان سيعيد الفشل بصيغة أخرى.
- **لم تُضَف خدمة S3 بديلة مجهولة السحب** (SeaweedFS/Garage/LocalStack): تبديل مخزن كامل بلا إمكانية
  تجربته محلياً (لا `docker` في هذه البيئة) هو مخاطرة أكبر من الحاجة المقيسة؛ والحاجة المقيسة هي «تصعد
  الحاوية، وترد الواجهة، ويعمل الترحيل والـseed والدخول» — وهذا محقَّق بالمخزن المحلي.
- **لم يُحذَف دعم S3** من الشيفرة ولا من الإعدادات: هو مسار الإنتاج، والاختبارات تغطيه عبر الملفات
  (`tests/` تبني مخزناً مؤقتاً على القرص؛ عقد الـS3 نفسه مثبَّت في واجهة `storage()`).

## الأثر

- `infra/docker-compose.yml`: ٨ خدمات بدل ١٠؛ `migrate` يعتمد على `postgres` فقط.
- `infra/backend.Dockerfile`: سطر إنشاء `/srv/objects`.
- `.github/workflows/ci.yml`: قائمة الصور المطلوبة في التشخيص، وتسجيل مخرجات `up`.
- `infra/README.md`، `docs/runbook.md`، `README.md`: وصف المكدّس صار مطابقاً لما يُشغَّل فعلاً.
- `docs/STATUS.md` §3: الدليل الحرفي من CI وسبب القرار.
