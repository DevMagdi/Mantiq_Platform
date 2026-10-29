# 008 — شاشات المرحلة الثانية خلف flag يُقرأ من قاعدة البيانات

**الحالة:** مقبول · **التاريخ:** 2026-10-26

## السياق
§9 يضع شاشات البوليصات والإصدار والتقارير ضمن «المرحلة الثانية»، والمطلوب أن تكون الواجهة كاملة
لكن بحالة «غير مفعّلة بعد»، بلا كود ميت وبلا نشر منفصل.

## القرار
- `feature_flags` جدول في القاعدة — لا متغيرات بيئة: التبديل يجب أن يكون مرئياً وقابلاً للتدقيق.
  `GET /api/v1/feature-flags` و`PATCH /api/v1/feature-flags/{key}` في `app/api/v1/endpoints/users.py`،
  والتبديل للأدمن فقط ويكتب `audit_logs` قبل/بعد.
- العلم الوحيد اليوم `phase2.enabled`، ومسارات المرحلة الثانية في `app/api/v1/endpoints/phase2.py`
  ترفع `FeatureDisabled` ⇒ **503** `{"code":"feature_disabled"}` عند إطفائه — الحماية ليست في
  الواجهة فقط (المواصفة تقترح 403؛ فرّقنا «ميزة مطفأة» عن «لا صلاحية»).
- على الواجهة: `features/phase2/phase2Gate.tsx` يقرأ `GET /feature-flags` عبر TanStack Query؛
  المسار المُطفأ يعرض حالة `phase2.disabled` بنصّ «غير مفعّلة بعد» بدل المحتوى، والشاشة نفسها
  (أعمدة، فلاتر، ألوان التقارير) مكتوبة كاملة وتشتغل بمجرد رفع العلم.
- `usePhase2.ts` يقرأ `/feature-flags` و`phase2Gate.tsx` يعرض «غير مفعّلة بعد» مع زر «تفعيل العلم»
  لمن يملك `settings.manage` فقط.
- `e2e/screenshots.spec.ts` يرفع العلم في `beforeAll` عبر الـ API ثم يلتقط الشاشتين، و
  `e2e/screens.spec.ts` يفحص الحالتين (مطفأ ⇒ رسالة الإطفاء، مفعّل ⇒ البيانات)، فلا تعتمد اللقطات
  على تبديل يدوي قبل CI.
- التغطية: `tests/test_dashboard_phase2.py::test_phase2_is_flag_gated_and_flag_changes_are_audited`.

## الأثر
تجربة التفعيل: `PATCH /api/v1/feature-flags/phase2.enabled {"enabled": true}` ثم إعادة التحميل —
بلا إعادة نشر. الفارق المسجّل عن الورقة في `docs/STATUS.md`.
