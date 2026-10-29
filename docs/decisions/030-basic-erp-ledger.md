# ADR-030 — دفتر حركات بسيط + مستحقات + عمولات + صندوق، ولوحة إدارة تنفيذية (R10)

- الحالة: مقبول (2026-10-28) · جولة R10
- المرجع: `backend/app/models/finance.py` · `backend/migrations/versions/0009_finance_ledger.py` ·
  `backend/app/services/{finance,executive}.py` · `backend/app/api/v1/endpoints/{finance,executive}.py` ·
  `backend/app/domain/permissions.py` · `backend/tests/test_finance.py` ·
  `frontend/src/features/{executive,finance}`

## 1) المشكلة

قبل R10 كان «المال» في المنصة **حقيقة مشتقّة** لا صفًّا: قسطٌ مدفوع = `installments.paid_at`،
وعمولة = `commissions.status`. لا يوجد مكان يُسأل منه:

- «كم حصّلنا هذا الشهر؟» — لا جدول يضم حركة بمعناها وتاريخها، ولا طريقة لتمييز نقدي من تحويل،
- «كم على العميل × ومتأخر كم يومًا؟» — الأعمار تُشتق في المتصفح، فقارئان يختلفان،
- «هل دُفعت هذه العمولة مرتين؟» — `commissions.status` لا يحكي عن الدفعة الثانية إلا بالسكوت،
- «ما رصيد الصندوق؟» — لا وجود لصندوق في القاعدة إطلاقًا،
- «كيف يبدو الشهر على مستوى الإدارة؟» — الشاشات تجيب عن الطلبات فقط، وبعض أرقامها مصفوفة في الواجهة.

النتيجة أن أي «تقرير مالي» كان إعادة حساب في الواجهة، وأي إلغاء كان محوًا للأثر.

## 2) القرار: صفٌّ واحد لكل حركة، والرصيد مجموع

`ledger_entries` هو المصدر الوحيد لكل رقم مالي. كل رصيد على أي شاشة هو **مجموع صفوف `status='posted'`**،
ولا يوجد عمود «رصيد» مخزَّن يمكن أن ينحرف:

| العمود | القاعدة |
|---|---|
| `entry_no` | `LE-000123` من عدّاد مقفول (`request_number_counters.key='ledger'`) — مُقروء للإنسان ومرتّب. |
| `entry_type` | `premium_receivable` · `commission_receivable` · `commission_payable` · `expense` · `adjustment` — لا نوع سادس. |
| `direction` | `in` / `out` هو الذي يحمل الإشارة. `amount > 0` قيدٌ في القاعدة (`ck_ledger_amount_positive`)، والمبلغ السالب مرفوض في الـschema (422) وفي الخدمة (422). |
| `status` | `posted` / `void`. **لا حذف**: `POST /finance/ledger/{id}/void` يغيّر الحالة ويكتب السبب والمستخدم والوقت، والصف يبقى للأبد. |
| `method` | `cash` / `bank` / `wallet` / `other`، و`NULL` حيث لا معنى له (مستحق لم يُقبض بعد). |
| الأطراف | `client_id` / `policy_id` / `installment_id` / `user_id` (المستفيد أو من حصّل) / `posted_by`. |

**اتجاه النوع مغلق:** `check_direction()` يرفض `premium_receivable` بـ`out` أو `expense` بـ`in` قبل أن
يُكتب الصف — خطأ نموذج في النموذج لا يتحول إلى رقم تحصيل في الشاشة.

**قيد التكرار:** فهرس فريد جزئي على `(entry_type, installment_id) WHERE status='posted'` (SQLite وPostgreSQL
يدعمانه). الخدمة تُرجع 409 أولًا، والفهرس يجعل السباق مستحيلًا؛ والإلغاء يحرّر الخانة لأن الصف الملغى لم يعد
يطابق الشرط.

## 3) نقطة الكتابة الوحيدة للتحصيل هي الدفع

`services/installments.pay()` هي المكان الوحيد الذي يتحول فيه قسط إلى `paid`، فكُتب القيد **في نفس
المعاملة** بعد `paid_at` مباشرة: قسط مدفوع بلا قيد في الدفتر هو بالضبط الانحراف الذي طُلب إنهاؤه.
والمسار يقبل جسمًا اختياريًا `{method}` (افتراضيه `cash`) حتى لا يُكسر عميل قديم يرسل بلا جسم.

كما أُضيف `policies.issued_at` (0009) لأن `created_at` هو تاريخ **حجز المسودة**؛ وثيقة بقيت
`pending_insurer` عبر حدّ شهر كانت ستُحسب «صادرة هذا الشهر» في الشهر الخطأ — وهذا نوع الأرقام الخطأ الذي
لا يجوز أن يظهر على شاشة الإدارة. الصفوف الصادرة القديمة تُملأ من `created_at` في نفس الترحيل.

## 4) العمولات: الاستحقاق شيء والدفع شيء آخر

`commissions.status` (accrued/settled/clawed_back) هو **الاستحقاق** الذي تقرأه شاشة الوثائق، ولا يُعاد
تعريفه. الدفعة لها جدولها: `commission_settlements` بحالات `accrued → approved → paid` (أو `cancelled`)،
و`commission_id` **فريد**: عمولة واحدة = دفعة واحدة، حتى لو تزامن طلبان. عند `pay`:

1. يُرفض ما ليس `approved` (409، ويسمّي الحالة التي وجدها),
2. يُكتب قيد `commission_payable` باتجاه `out` في نفس المعاملة,
3. تُضبط `settlement.status=paid` + `ledger_entry_id`، و**أيضًا** `commissions.status=settled` كي لا تتعلم
   الشاشة القديمة جدولًا جديدًا،
4. تدقيق `commission.approved` / `commission.paid` على صف التسوية.

الصف يُنشأ عند أول `approve`/`pay` (لا كتابة داخل `GET`): القائمة تدمج `left join` وتُظهر العمولة
`accrued` حتى لو لم يكن لها صف تسوية بعد.

## 5) الصندوق: نقدي فقط، والباقي معلن لا مخفي

`GET /finance/cashbox` → `balance = Σ(in, method=cash, posted) − Σ(out, method=cash, posted)`.
التحويل البنكي مالٌ حقيقي لكنه ليس في الدرج وقت الإقفال، فرصيد يضمّه لا يمكن عدّه؛ لذلك `by_method`
يعرض الأربع طرق كاملة (داخل/خارج/رصيد) بدل طمسها. `today_in/out` و`month_in/out` من نفس الاستعلام.

**الإلغاء يعكس الرصيد «مجانًا»** لأن كل مجموع يفلتر `posted` — لا كود عكسي يمكن أن ينسى نفسه.

## 6) العزل والصلاحيات

صلاحيات جديدة: `finance.view` · `finance.post` · `finance.void` · `commissions.manage` · `reports.executive`.

| الدور | ما يحصل عليه |
|---|---|
| `system_admin` | كل شيء (من `ALL_PERMISSIONS`). |
| `sales_manager` | الخمسة كلها — مدير المكتب هو مالك المال في وسيط صغير. |
| `account_manager` | `finance.view` فقط. |
| `sales_rep` / `customer_service` | **لا شيء**: 403 على الدفتر والصندوق والمستحقات ولوحة الإدارة. |
| بوابة العميل | 403 — لا مسار مالي في البوابة. |

العزل ليس زرًّا مخفيًّا: `ledger_predicate` يربط صف الدفتر بعميله، و`commission_predicate` بوثيقته، مرورًا
بـ`with_scope` كبقية الجداول. صفوف المكتب (مصروف بلا عميل) **يراها المدير فقط** لأن نطاق المندوب لا يملك
عميلًا يطابقها. وقراءة معرف خارج النطاق = 404 لا 403 (نفس قاعدة R4).

## 7) لوحة الإدارة التنفيذية: كل رقم من استعلامه

`GET /api/v1/executive/dashboard` بشكل ثابت: `period` · `kpis` · `sales_performance` · `ar_aging` ·
`channel_mix` · `renewals_next_30d` · `alerts`.

| KPI | المصدر الحقيقي |
|---|---|
| `collections_mtd` | `SUM(ledger_entries.amount)` حيث `posted`, `direction=in`, `posted_at >= بداية الشهر`. |
| `collections_target` | **`null` دائمًا** — لا نموذج أهداف تحصيل في هذه الجولة؛ نسبة بمقام مخترع تجعل بسطًا حقيقيًا كذبًا. |
| `outstanding_ar` / `overdue_ar` | نفس استعلام `GET /finance/ar/summary` (أقساط غير مدفوعة على وثائق `issued` فقط؛ المسودة لا تدين بشيء). |
| `commissions_accrued` | `SUM(commissions.amount)` حيث `status='accrued'`. |
| `commissions_paid_mtd` | `SUM(commission_settlements.amount)` حيث `paid` و`paid_at` داخل الشهر. |
| `open_requests` / `overdue_requests` | `ServiceRequest` بحالات `RequestStatus.open_statuses()` / و`is_overdue`. |
| `new_leads_mtd` / `converted_leads_mtd` | `Lead.created_at` / `Lead.converted_at` داخل الشهر. |
| `policies_issued_mtd` | `status='issued'` و`COALESCE(issued_at, created_at)` داخل الشهر. |
| `cashbox_balance` | `GET /finance/cashbox` نفسه (`balance`). |

`ar_aging` هو نفسه `buckets` من خدمة المستحقات: `current` + `d0_30` (0–30) + `d31_60` (31–60) +
`d61_90` (61–90) + `d90_plus` (90+). «غير المستحق بعد» له دلو خاص به، لأن ضمّه إلى «متأخر 0–30» رقم خاطئ.

`alerts` أكواد ثابتة (`overdue_ar` · `overdue_requests` · `commissions_to_approve`) بعدد حقيقي من نفس
الاستعلامات؛ الواجهة تترجم الكود وتستخدم رسالة الـAPI كاحتياط.

## 8) خارج النطاق (صريح)

الضرائب والفاتورة الإلكترونية ومصلحة الضرائب (ETA) · ربط البنوك والتسوية البنكية · المخزون · الموارد
البشرية والرواتب · تعدد الشركات/العملات (العملة `EGP` مثبّتة كنص) · الأصول الثابتة · أهداف التحصيل
المعقّدة (`collections_target` يبقى `null`) · إعادة بناء Leads/Inbox/Integrations.

## 9) ما يثبته الاختبار

`backend/tests/test_finance.py` (9): دفع قسط يكتب قيدًا واحدًا؛ الإلغاء يعكس رصيد الصندوق ويُبقي الصف
ويُدقَّق؛ عمولة لا تُدفع مرتين؛ دلاء الأعمار؛ أرقام لوحة الإدارة تُطابق استعلامات مستقلة؛ مندوب بلا
`finance.view` = 403؛ مبلغ غير موجب = 422؛ AR لعميل خارج النطاق = 404؛ كل حركة لها تدقيق.
و`frontend/tests/financeRound.test.tsx` يثبت أن الشاشة تطبع أرقام الـAPI ولا تخترع هدفًا، وأن أزرار
الاعتماد/الصرف/الإلغاء تتبع الصلاحية والحالة.

## 10) تعديل R10.1 — الإلغاء يعيد الواقعة، و`issued_at` لا يُخمَّن

**10.1 الإلغاء يعيد ما دفعته الحركة، في نفس المعاملة.** القرار الأصلي (§2) قال «الإلغاء حالة، وكل مجموع
يُرشِّح `posted`» — وهذا صحيح للمجاميع وخاطئ للوقائع: إيصال قسط أُلغي كان يخرج من الصندوق ومن المديونية
بينما القسط يبقى `paid`، فيصير كشف الوثيقة مخالفًا للدفتر ويجيب الدفع مرة أخرى بـ`409`. التعديل:

| القيد الملغى | ما يُعاد في نفس المعاملة | التدقيق |
|---|---|---|
| `premium_receivable` + `installment_id` | `paid_at = NULL`، والحالة `late` أو `upcoming` حسب تاريخ الاستحقاق | `installment.reopened` (السبب + رقم الإيصال) |
| `commission_payable` + تسوية | التسوية `approved` و`paid_at/by = NULL` و`ledger_entry_id = NULL`، والعمولة `accrued` | `commission.reopened` |
| مصروف/تعديل | لا تابع ⇒ إلغاء محض | `ledger.voided` |

الفهرس الفريد الجزئي `(entry_type, installment_id) WHERE status='posted'` يبقى كما هو: الإلغاء يحرّر
الخانة فعلًا، والدفع مرة أخرى يكتب إيصالًا جديدًا برقم `entry_no` جديد. الاستجابة تحمل `reopened`، و`409`
الدفع المزدوج يسمّي الإيصال (`receipt_entry_id`) وطريق الرجوع.

**10.2 `policies.issued_at` من سجل التدقيق، والمجهول يبقى مجهولًا.** `0009` ملأ العمود بـ`created_at`
(تاريخ حجز المسودة) — وهو الخطأ الذي أُنشئ العمود لمنعه. الترحيل `0010_policy_issue_date` يأخذ التاريخ من
`audit_logs` حيث `action='policy.issued'`، ويترك `NULL` لما لا صف تدقيق له، و`policies_issued_mtd` لم يعد
يستخدم `coalesce(issued_at, created_at)`: يعدّ التواريخ المسجَّلة فقط، ويبلّغ عن المجهولة في تنبيه
`issue_date_unknown`. المبدأ: **العدّ الناقص الصادق أفضل من شهر لا أحد سجّله.**

**10.3 الصندوق رصيد حركة، لا رصيد درج.** لا أرصدة افتتاحية ولا إقفال يومي في النموذج، والشاشة تقول ذلك
بنصّه وتعرض `since` (أول حركة نقدية مُسجَّلة) بجانب `as_of`؛ عنوان البطاقة «صافي حركة النقدية (وارد − صادر)».
إن احتاج العمل لاحقًا رصيدًا افتتاحيًا أو إقفالًا، فذلك جدول جديد وقرار عمل — لا يُشتق ضمنًا من هذه الأرقام.

**10.4 توقيت استحقاق العمولة قرار عمل مفتوح.** §4 يوثّق السلوك الحالي (الاستحقاق عند الإصدار) ولا يقرّره
نيابة عن المالك: الخيارات الثلاثة (عند الإصدار / عند التحصيل / عند الإصدار مع استرداد) معروضة في
§9.7 من تقرير R10، والتنفيذ ينتظر اختيارًا صريحًا.
