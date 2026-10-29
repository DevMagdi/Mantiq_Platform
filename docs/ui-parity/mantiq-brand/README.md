# لقطات هوية Mantiq (R13 / ADR-034)

خمس شاشات + لقطة هاتف، بنفس البيانات التجريبية (`python -m app.seed`) وبواجهة `arena/5e32b1cd-mantiq-platform`:

| الملف | الشاشة | ما يجب أن يُرى |
| --- | --- | --- |
| `01-login.png` | تسجيل الدخول (1440×900) | لوحة داكنة Deep Navy + الماركة «منطق / MANTIQ TECHNOLOGIES»، بطاقة بيضاء، زر أساسي ذهبي ونصّه Deep Navy |
| `02-dashboard.png` | لوحة التحكم | شريط جانبي Deep Navy، عنصر نشط ذهبي مع شريط على الحافة اليمنى (RTL)، صفحة `#FAFBFC`، بطاقات بيضاء |
| `03-inbox.png` | صندوق الطلبات | نفس الشريط النشط، فقاعات الرد الصادرة ذهبية بنص Deep Navy |
| `04-erp-hub.png` | ERP المحاسبي | تبويب الفترة النشط ذهبي، جدول ومبالغ بنصوص Navy |
| `05-portal.png` | بوابة العميل | بانر Deep Navy بترويسة الماركة، بطاقات بيضاء |
| `06-login-mobile.png` | تسجيل الدخول (390×844) | الماركة على الأبيض (اللوحة الداكنة غير مرسومة على الهاتف)، زر ذهبي |

## كيف أُعيد توليدها

```bash
# 1) الواجهة والـAPI يعملان (بيانات التجربة موجودة)
cd backend && python -m alembic -c alembic.ini upgrade head && python -m app.seed
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
cd frontend && npm run dev

# 2a) في CI/بيئة فيها Chromium الخاص بـPlaywright
node scripts/capture-brand-screens.mjs

# 2b) أو على متصفح شغّلته بنفسك (sandbox بلا CDN)
CDP_URL=http://127.0.0.1:9222 node scripts/capture-brand-screens.mjs
```

بيانات الدخول الافتراضية في السكربت هي بذرة R13 (`manager@mantiq.test` / `Mantiq!2026`)، ويمكن تجاوز
كلمة المرور بـ`BRAND_PASSWORD` عند تشغيله على بيئة ضبطت `SEED_PASSWORD`. آخر تحديث للقطات: بعد
إعادة تسمية بيانات العرض، فبطاقة الدخول تعرض `@mantiq.test` و`Mantiq!2026` ولا شيء من الاسم القديم.

السكربت لا يشغّل خدمات بنفسه (فلا يصوّر قاعدة فارغة بصمت)، ويطبع لكل لقطة العنوان المحسوب
ولون الـ`body` ولون الشريط الجانبي، ويُرجع رمز خروج غير صفري إذا ظهر أي خطأ في الـconsole.

اللقطات نفسها **ليست** دليل مطابقة بيكسل — الحكم البصري البشري يبقى في
[`../README.md`](../README.md)، والقيم القابلة للقياس تُفرض في `frontend/tests/tokens.test.ts`
و`frontend/tests/favicon.test.ts` و`frontend/e2e/brand.spec.ts`.
