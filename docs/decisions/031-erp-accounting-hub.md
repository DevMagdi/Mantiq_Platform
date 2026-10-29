# ADR-031 — ERP المحاسبي: مركز واحد + صرف حسب الفترة + void يعيد فتح القسط

التاريخ: 2026-10-28 · الحالة: مقبول · يبني على ADR-030

## القرار
1. بند سايدبار واحد «ERP المحاسبي» (`/erp`) يظهر بـ `finance.view` أو `reports.executive`؛ المسارات العميقة
   (`/finance/*`, `/executive`) تبقى للإشارات المرجعية وتُفتح من كروت داخل `/erp`.
2. `GET /finance/spend-by-period?period=month|quarter|semiannual|year` (قيم أخرى → 422): totals posted فقط
   من تجميع واحد مجمّع `entry_type × direction`، ويعيد الخمسة أنواع دائمًا حتى لو صفرية.
3. void لقيد `premium_receivable` مربوط بقسط يعيد فتحه: `paid_at=null` و`status=late|upcoming` من `due_date`،
   ويُسجَّل `installment_reopened` في audit `ledger.voided` — وإلا بقي القسط "مدفوعًا" رغم زوال إيصاله.
4. الصندوق يصرّح `is_net_movements_only=true, balance_note="net_movements_only"` والواجهة تعرض التنويه حرفيًا:
   لا رصيد افتتاحي ولا إقفال يومي بعد — الرقم ليس رصيدًا محاسبيًا مقفولًا.

## خارج النطاق
دليل حسابات/قيود مزدوجة/ميزان مراجعة/مخزون/رواتب — كما في التكليف، لا تُبنى الآن.

## الأداء
حمل Locust موثّق في `docs/perf/erp-load-2026-10-28.md` (50,004 قيدًا posted): بوابة spend-by-period نجحت
(p95 530ms < 800ms)، وledger list جاء p95 730ms فوق بوابة 600ms على عامل uvicorn واحد و2vCPU — موثّق
كفشل بوابة على هذا العتاد لا كنجاح.

## تحديث الدمج مع R10.1 (PR #14 دُمج أثناء هذه الجولة)
عند دمج `main` ظهر أن R10.1 نفّذ void↔installment أعمق (إعادة فتح تسوية العمولة + audit منفصل
`installment.reopened`). اعتُمد تنفيذه كما هو وحُذف التكرار من هذه الجولة؛ بقي من R11: الـ hub والفترات
وحقلا `is_net_movements_only/balance_note` (إضافةً إلى `since` من R10.1). لا migration جديدة من R11؛
ترحيل R10.1 ‏`0010` قائم كما هو.
