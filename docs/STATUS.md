# STATUS — منصة إدارة الوساطة التأمينية (Broker Hub)

## Product Scorecard Sprint — 2026-10-29 (ADR-033)

| البُعد | قبل | بعد (مقدّرة بدليل) | الدليل |
|---|---|---|---|
| اكتمال تشغيلي | 7.5 | **8.5** | مسار A كامل باختبار آلي: وارد واتساب → رد على القناة → حالة → إغلاق + `first_response_at` (`tests/test_score8.py::test_after_sales_full_path_whatsapp_to_closed`)؛ لوحة «اليوم» (SLA متأخر/متابعات ليد/أقساط 14 يوم) من `GET /dashboard/today` على الـDashboard |
| جاهزية بيع | 6.5–7 | **8** | `/setup` معالج مالك من 6 خطوات بحالة اتصال القنوات حية، `/ops` صحة تشغيل، `docs/PILOT_PACK.md` (90 يوم، متطلبات، معايير نجاح)، نصا خصوصية/موافقة تسجيل داخل المنتج والـrunbook |
| جودة هندسية | 8.5 | **8.5** (بلا تراجع) | فهارس 0011 + تجميع الصندوق؛ حمل قبل/بعد في `perf/product-score8-2026-10-29.md`: صفر 5xx عند 50k قيد + 20k طلب. **جولة إصلاح الأداء (s9b)**: إزالة N+1 قائمة الطلبات (9 استعلامات→2) وتجميع `sales_performance` في SQL — البوابتان خضراوان الآن حتى على هذا العتاد: دفتر p95 ‏160ms ≤ ‏600ms وقائمة طلبات p95 ‏250ms ≤ ‏400ms، بلا تخفيف لأي بوابة؛ العزل وvoid↔قسط بلا تغيير واختباراتهما قائمة |
| تجربة منتج | 6 | **8** | /erp: تصدير Excel + بطاقة إقفال + تنويه «تشغيلي لا قانوني»؛ الصندوق: اعتماد افتتاحي؛ Dashboard: لوحة اليوم؛ حالات loading/error/forbidden/empty على الشاشات المعدّلة؛ e2e ERP أخضر في CI |
| ERP تشغيلي | 5 | **8** | رصيد افتتاحي (جدول مستقل، الرصيد = افتتاحي ± حركات)، إقفال شهر (حظر 409 + تجاوز مُدقَّق لحامل `finance.period_close`)، تصدير فترة Excel بنفس تجميع الخادم، اختبارات `tests/test_score8.py` الأربع |

أوامر التحقق: `pytest tests/test_score8.py` (5 ناجحة) · الحزمة الكاملة `pytest` خضراء · `ruff/mypy` نظيفان · `vitest` 133 · لوحات الحمل بالأوامر الحرفية في تقرير perf (الخام `docs/qa/load/raw/s9b-*`).
**لقطات قبل/بعد للشاشات الخمس الحرجة**: `docs/screenshots/score8/` (قبل من واجهة `main`، بعد من واجهة السبرينت، نفس البيانات والـAPI — الطريقة موثقة في README هناك).
ما زال يحتاج عميلًا حيًا للوصول لـ10: أزمنة الاستجابة على عتاد إنتاجي، اعتماد نصوص الامتثال قانونيًا، وحجم بيانات مكتب حقيقي عبر 90 يوم Pilot.

آخر تحديث: 2026-10-28 · الفرع: `arena/05085e84-insurance-platform-new`.
جولة R12 (**Channels Live + Request Media + Brand + Strict Load**، ADR-032): الإعدادات بلا أي مفتاح
«تحويل للحيّ/محاكاة» — حالة اتصال فقط (متصل/غير متصل/خطأ) مع تسمية الحقول الناقصة و`آخر ويبهوك/آخر خطأ`؛
واتساب وارد بصري/مستند/صوتي/فيديو على خط الطلب الزمني ونفس نموذج المرفقات بروابط موقّعة مدقَّقة؛
بريد وارد عبر ويبهوك موقّع `POST /webhooks/email` (`X-Email-Signature` بـ `inbound_secret`) يفتح/يُلحق
طلبات قناة email؛ وfavicon Broker Hub كامل (ICO+32+180+192+512+manifest) مع ترويسات nginx.
الحمل الصارم: 20,100 طلب + 100,004 قيد، مختلط 50 مستخدمًا × 12 دقيقة، صفر 5xx وصفر مضاعفات وعزل
403/403 — لكن بوابات زمن القائمة/الويبهوك تحت المزيج وERP عند 100k **FAIL on this hardware** بالأرقام
في [`perf/r12-load-2026-10-28.md`](perf/r12-load-2026-10-28.md)، بلا تخفيف لأي بوابة.

آخر تحديث: 2026-10-28 · الفرع: `arena/4bf03e2e-insurance-platform-new`.
جولة R11 (**ERP المحاسبي**): بند سايدبار واحد «ERP المحاسبي» → `/erp` (يظهر بـ `finance.view` أو
`reports.executive`، والمسارات العميقة باقية من كروت الشاشة)، و`GET /finance/spend-by-period` للفترات
الأربع (شهر/ربع/نصف سنة/سنة، posted فقط، وغير المعروف 422)، وvoid لقيد تحصيل قسط يعيد فتح القسط
(`paid_at=null` + حالة late/upcoming) مع تسجيله في audit، والصندوق يصرّح `is_net_movements_only`.
الحمل: 50,004 قيدًا posted و20 مستخدمًا × 3.5 دقيقة بلا أي فشل، و`spend-by-period` p95 ‏530ms،
لكن `ledger` list p95 ‏750ms فوق بوابة 600ms على عامل واحد/2vCPU — فشل بوابة موثّق بسببه في
[`perf/erp-load-2026-10-28.md`](perf/erp-load-2026-10-28.md)، لا نجاح مُدّعى. التفاصيل ADR-031.

آخر تحديث: 2026-10-28 · الفرع: `arena/f0ab7024-insurance-platform-new` (R10.1).
جولة R10.1 (تصحيح على ثماني نقاط مراجعة): الإلغاء صار يعيد **الواقعة** لا المجموع فقط (القسط يعود غير
مدفوع، والتسوية تعود `approved`)، و`policies.issued_at` صُحِّح من سجل التدقيق في ترحيل جديد `0010` بعد أن
كان `0009` يملؤه بـ`created_at`، وشاشة الصندوق تكتب أنها **صافي حركة** بلا رصيد افتتاحي وبلا تقفيل يومي،
ووحدات المال الثلاث دخلت بوابة التغطية. اختبار `r6-round` (≤2 ثانية) **موقوف ومسجَّل** حتى تُحسم آلية
الرد الذي سبق التغيير — القسم §9 من تقرير R10. **لا ميرج في هذه الجولة بلا كلمتك.**

جولة R10 (**ERP أساسي + لوحة إدارة تنفيذية**): المال صار صفًّا في دفتر لا رقمًا مشتقًّا — `ledger_entries`
تُكتب مع كل حركة (دفع قسط، مصروف، تسوية، صرف عمولة)، والإلغاء حالة تبقى بأثرها وسببها ولا تحذف صفًّا،
و`amount > 0` قيدٌ في القاعدة و`direction` هو ما يحمل الإشارة. الفهارس الفريدة الجزئية تمنع إيصالين لنفس
القسط ودفعة عمولة ثانية. أربع وحدات فقط (دفتر، مستحقات بأعمار 0–30/31–60/61–90/90+، عمولات
اعتماد→صرف، صندوق نقدي) + شاشة «لوحة الإدارة التنفيذية» التي كل رقم فيها استعلام حيّ، و`collections_target`
تظل `null` لأن لا نموذج أهداف موجود (لا نسبة بمقام مخترع). العزل: `finance.view` غير موجودة للمندوب ⇒ 403
على الدفتر والصندوق والمستحقات ولوحة الإدارة. التفاصيل في
[`qa/r10-basic-erp-2026-10-28.md`](qa/r10-basic-erp-2026-10-28.md) و[ADR-030](decisions/030-basic-erp-ledger.md).

جولة R9 (الإنتاج حيّ فقط + مكالمات الموبايل): التكاملات في الإنتاج لا تعمل بالمحاكاة إطلاقًا — `ENV=prod`
يفرض `live`، والسائقان المحاكيان غير قابلين للبناء خارج dev/test، ومسارات `/api/v1/dev/sim*` غير موجودة في
بناء الإنتاج، ومزوّد حيّ ناقص الأسرار يُفشل الإقلاع برسالة تسمّي المفتاح (لا سقوط صامت على sim). قناة
المكالمات أُعيد تسميتها وتثبيتها كـ**مكالمات موبايل** (`/webhooks/mobile-calls`، مزوّد Twilio موثَّق +
بدائل) وتُفتح منها طلبات بقناة `call` بمفتاح رقم الموبايل. التفاصيل في
[`qa/r9-production-live-2026-10-28.md`](qa/r9-production-live-2026-10-28.md) و[ADR-029](decisions/029-production-live-only-integrations.md).

آخر تحديث للسجل السابق: 2026-10-27 · فرع R6: `arena/a961502a-insurance-platform-new` من `main` عند `c73fcb5`.
هذه الجولة = **R6 (Realtime · Overdue · Live switch)**: ثلاث ميزات جاهزة للإنتاج — «طلبات متأخرة» صار
باباً يفتح طابوراً مفلتراً قابلاً للمشاركة، وكل تغيير يصل كل تبويب مصرّح **خلال ثانيتين بلا تحديث**، ومفتاح
«التحويل إلى الوضع الحيّ» لا يعيد 409 في المسار السعيد. التفاصيل في
[`qa/realtime-overdue-live-2026-10-27.md`](qa/realtime-overdue-live-2026-10-27.md) وADRs [026](decisions/026-realtime-change-feed.md)
و[027](decisions/027-live-switch-preflight.md).
الجولة السابقة = **R5 (Live-Real)**: القنوات الحيّة تعمل فعلاً (لا محاكاة)، إصدار الوثيقة يُنتج ملفاً قابلاً
للتحميل من البوابتين، فلاتر المدير على الليدز في الـAPI والواجهة، وحسابات بوابة العميل باسم مستخدم —
التفاصيل في [`qa/live-real-2026-10-27.md`](qa/live-real-2026-10-27.md) وADRs 021–025.
الجولة السابقة = **R4 (Live-Ready QA)**: مسح شامل لكل مسار تشغيلي بكل دور، ثم إصلاح كل ما سقط — التفاصيل
في [`qa/live-ready-2026-10-27.md`](qa/live-ready-2026-10-27.md). الجولة السابقة = **R3**: إصلاح جذري لـ e2e المتبقّي + إقلاع `docker compose` + هاردننغ استيراد/إعادة
توزيع/مقاعد بحزام اختبارات أوسع. القاعدة نفسها من R0: لا يُكتب «تم» إلا مع الأمر ومخرجاته، وما لا
يُشغَّل يُكتب «لم يُشغَّل» مع سببه الحرفي.

جولة R2 (السابقة) = مقاعد مكتب المبيعات، استيراد Leads من ملف يكتب في القاعدة فعلًا، وزرّ «إعادة توزيع
الـLeads» يحرّك الحوض كله — تفاصيلها باقية في هذا الملف وفي ADR-017.

مرجع التحقق: ملف تصميم الواجهات (16 شاشة) المرفق من صاحب المشروع + نصّ المواصفات §1–§13.
قاعدة العمل: لا يُكتب «مطابقة» إلا بموافقة المالك — انظر [`ui-parity/`](ui-parity/README.md).

## حالة المراحل

| مرحلة | الحالة | ما يثبت ذلك |
| --- | --- | --- |
| M0 توثيق القرار والهيكل | ✅ منتهية | [`decisions/001-architecture.md`](decisions/001-architecture.md) + هيكل المستودع (`backend/ frontend/ infra/ docs/ .github/`) |
| M1 التخطيط: الجلسات والصلاحيات والعزل | ✅ منتهية | `app/core/{security,scope,deps}.py` · 33 جدول · `tests/test_isolation_matrix.py` (404 لكل id خارج النطاق) · ADR-005 |
| M2 الهياكل المرجعية (شركات/أنواع/مستندات/أسباب فقد/ساعات عمل/SLA) | ✅ منتهية | `app/models/masters.py` · `app/data/insurers_eg.csv` (37 شركة مرخّصة من السجل الرسمي) · `tests/test_masters.py` |
| M3 قنوات الورود (واتساب/مكالمات) + محاكي + SLA | ✅ منتهية | `app/integrations/*` · `webhooks.py` (توقيع + idempotency) · `tests/test_intake.py` · `GET /api/v1/dev/sim` لوحة محاكاة حقيقية |
| M4 الطلبات: صندوق موحّد + ردود + ملاحظات داخلية + تسجيلات | ✅ منتهية | `services/requests_svc.py` · `tests/test_requests_flow.py` `test_requests_svc.py` · روابط موقّعة قصيرة العمر + تدقيق لكل استماع |
| M5 المبيعات: Leads/Kanban/تحويل ذرّي | ✅ منتهية | `services/leads.py` (`convert` في معاملة واحدة + idempotent) · `tests/test_leads_flow.py` |
| M6 العملاء وملف العميل والمؤمَّن عليهم + Excel | ✅ منتهية | `services/clients.py` · `tests/test_import_insured.py` `test_clients_crud.py` · معاينة→تأكيد→تقرير |
| M7 البورتال | ✅ منتهية | `services/portal.py` · `tests/test_portal.py` (اختبار يمنع تسرّب أي مفتاح حسّاس) |
| M8 المرحلة الثانية (وثائق/أقساط/عمولات/إصدار/تقارير) خلف flag | ✅ الإصدار يُنتج ملفاً (R5) | `services/policy_issue.py` + `services/documents.py` + `policy_documents` (0006) · `tests/test_policy_issue.py` · الشاشات 12–14 عند تفعيل `phase2.enabled` |
| M9 التوثيق والمحاكاة وتشغيل UI | 🟡 جزئي | هذا الملف + [`runbook.md`](runbook.md) + [`ui-parity/`](ui-parity/README.md) · لقطات الشاشة تُنتَج في CI فقط (انظر «ما لم يُشغَّل») |
| M10 الاختبارات النهائية + parity + تجهيز الدمج | 🟡 جارية | بعد R3: backend أخضر على SQLite (**198** + 9 مُتخطَّاة بعلامة `postgres_only`)، و**PostgreSQL 16 يقيسه CI** وحده في هذه البيئة · frontend أخضر (82 اختبار وحدة في 11 ملفاً + `build` ✓) · e2e: نوعان من الأدلة — typecheck محلي ✓، و**CI شغّلها فعلاً: 166 اختباراً · 131 ناجحاً · 0 فاشل · 35 مُتخطٍّ** (تعليق تلقائي بالأرقام على الـPR #6) · ruff (146 ملفاً)/mypy (107)/تغطية النواة (14/14)/استقرار الـOpenAPI (98 مساراً)/**تمرير كل النقاط** نظيفة |
| R1 مركز الإعدادات: تكاملات حقيقية + إنشاء وثيقة | ✅ منتهية (محلياً) | `/settings` بثلاث بطاقات + حيّ/محاكاة بتأكيد + سجلّ `webhook_events` + `POST /policies` بجدول أقساط وعمولة · `tests/test_integrations_settings.py` (16) · `frontend/tests/settingsView.test.tsx` (14) · ADR-016 |
| R2 مكتب المبيعات: مقاعد + استيراد Excel/CSV + إعادة توزيع الحوض | ✅ منتهية (محلياً؛ e2e تُقاس في CI) | `POST /leads/import` ⇒ `lead_import_jobs` + `POST /leads/assign-bulk` + `/sales-team/members` · `tests/test_lead_import.py` (10) `tests/test_team_members.py` (6) `tests/test_lead_redistribute.py` (11) · `frontend/tests/teamRound.test.tsx` (8) · `frontend/e2e/team-round.spec.ts` (4) · ADR-017 · جدول Proof حيّ في هذا الملف |
| R9 الإنتاج حيّ فقط: واتساب + مكالمات الموبايل بلا محاكاة | ✅ منتهية (اتصال ميداني ⬜ يحتاج أسرار المشغّل) | `ENV=prod` يفرض live و`sim` صريحة تفشل الإقلاع · `integrations/sims.py` يرفض البناء خارج dev/test · `registry.assert_production_ready()` يُفشل الإقلاع بلا أسرار · `/webhooks/mobile-calls` (Twilio + بدائل) يفتح طلبًا بقناة `call` · `dev_sim.py` يُركّب في dev/test فقط · `tests/test_production_live_only.py` (11) · `live_matrix` §4.9b (7 فحوص production) · ADR-029 |
| R10 ERP أساسي: دفتر حركات + مستحقات (أعمار) + عمولات + صندوق + لوحة إدارة تنفيذية | ✅ منتهية (محلياً؛ e2e تقيسها CI) | `models/finance.py` + `0009_finance_ledger` · `services/{finance,executive}.py` · `endpoints/{finance,executive}.py` (11 مساراً) · `tests/test_finance.py` (9) · `frontend/tests/financeRound.test.tsx` (5) + 5 شاشات · ADR-030 |
| مزوّد AI (§11 M10): واجهة واحدة خلف `ai.enabled` + مراجعة بشرية | ✅ السطح كامل بسائق محاكاة · ⬜ بلا مزوّد حقيقي | `integrations/ai_sim.py` (حتمي، استخلاصي) · `endpoints/ai.py`: `GET /ai/status` · `POST /ai/calls/{id}/transcribe` · `POST /ai/requests/{id}/summarize` · `POST /ai/classify` · `tests/test_ai.py` (7) · زر «تلخيص آلي» في `InboxPage` يظهر فقط عند تشغيل الراية · ADR-013 |

## جولة R3 — ما كان أحمر، ولماذا، وما أُصلح

Round R3 ran in two passes. Pass 1 (`83f0c4f` + `607eb0a`) fixed the 13 failures measured on `main` and the
compose entrypoint the docker job rejected; its first CI run then measured **14** failures on the new head,
and pass 2 (this section) fixes the five distinct causes that run exposed. Both passes are recorded below,
because "we fixed 13" is only honest next to "the next run found 14".

### 0) الجولة الثانية: ما كشفه تشغيل CI على `607eb0a` (14 فشلاً)

| الفشل (من ملخّص الـPR الحقيقي) | السبب الجذري | الإصلاح |
|---|---|---|
| `screens` + `screenshots` (المشروعان) + `05-client-file.png` | `PolicyOut.policy_no` معرَّف `str` والعمود **nullable** و`PolicyCreate` يقبل الغياب ⇒ وثيقة مسودّة واحدة تُسقط رد `/clients/{id}/overview` كله بـ`ResponseValidationError` (500) | `policy_no: str | None` في المخطط + `—` في الواجهة + اختبار `test_a_draft_policy_without_a_number_still_opens_the_client_file` و`tests/clientFile.test.tsx` |
| `lead import › the preview writes nothing …` (المشروعان) | `ready` المقروء 1 بدل 2: عمود «الإيميل» لم يُطابق أياً من الأسماء المستعارة، فاختفى العمود كله وبقي الصف «جاهزاً» رغم بريده الفاسد | `_norm_header` يوحّد «ال/أ/إ/ى/ة» قبل المطابقة + تقرير «أعمدة لم يتم التعرف عليها» + اختباران جديدان |
| `lead import › broken and Arabic-heavy CSV rows` | نفس السبب أعلاه (2 جاهز بدل 1، و2 أخطاء بدل 3) | نفسه — والمخرجات صارت `rows 4 · ready 1 · errors 3` للصفوف `[3,4,5]` |
| `members › a manager adds a rep …` (سطح مكتب) | الحوار يسكن دوره من `assignable_roles[0]`، والقائمة كانت **أبجدية** ⇒ `account_manager` هو الافتراضي، فالمقعد المُضاف لا يستطيع حمل أي Lead | `ROLE_ORDER` في `services/team.py` (بياع أولاً) + الاختبار يختار «بياع» صراحةً + `test_the_role_list_offers_the_rep_seat_first` |
| `members` / `lead import` / `redistribute` (جوال) | مهلة 60s في نقرة: `element is outside of the viewport` ×114 — الحوار بلا سقف ارتفاع يتجاوز الشاشة، فتذييله خارج أي صندوق تمرير | إصلاح حقيقي في `components/Modal.tsx` (سقف `100dvh` + تمرير داخلي + تذييل ثابت) و`tests/modal.test.tsx`؛ ونطاق المشروعين موثَّق في ADR-019 |
| `dashboard › a rep without requests.view …` (جوال) | الاختبار يطلب «التنقل الرئيسي» وهو غير مرسوم على 390 (ADR-018) ⇒ 0 عناصر تُقرأ خطأً | الاختبار يسأل كل شكل عن تنقّله، والتأكيدات الثلاثة كما هي |


### 1) e2e: الفشل الـ13 على `main` عند `78085fa` (لقطة CI الحقيقية)

سُحبت القائمة من `gh api repos/DevMagdi/Insurance-Platform_new/check-runs/<id>/annotations` (لا من ذاكرة):
**13 غير متوقع · 89 ناجح · 29 مُتخطًّى + 15 لم تُشغَّل**. لكل فشل، السبب الجذري والإصلاح:

| الاختبار | السبب الجذري (بالمخرجات) | الإصلاح |
|---|---|---|
| client file (شاشة 5) + لقطة `05-client-file.png` | `GET /clients/{id}/overview` يرد **500** لأي عميل له وثيقة: الحقل `policy.insurance_type` كان `{code,name}` والمخطط يتوقّع `{id,code,name}` ⇒ `ResponseValidationError` يأكل الرد كله، والصفحة تعرض `notfound` | `services/clients.overview` يرسل `id` — وبند اختبار جديد `test_the_overview_of_a_client_that_has_a_policy_serialises_its_insurance_type` (لم يكن هناك اختبار واحد يقرأ overview لعميل **له** وثيقة) |
| dashboard (جوال) | عنوان KPI بصندوق `width: 0`: الهيكل يرسم شريطاً جانبياً 264px بلا كسر عند `lg`، فعلى 390px بقي للمحتوى 126px | هيكل متجاوب (ADR-018) + اختبار الهيكل يقيس الشكلين |
| inbox (سطح مكتب) | الردّ محفوظ و`first_response_at` لم يُقرأ في قراءة واحدة أُخذت فور إعادة الرسم | `expect.poll` على `GET /requests/{id}` (نفس نمط `settings.spec.ts`)، والتأكيد كما هو؛ والقاعدة مثبّتة في `tests/test_requests_flow.py` |
| isolation (المشروعان) | لافتة التحويل تطابق نصاً عاماً، وبعد أول تحويل تُصبح عنصرين ⇒ strict mode | اسم الـLead صار فريداً لكل تشغيل واللافتة تُطابَق باسمه (`toHaveCount(1)`) — تأكيد أقوى لا أضعف |
| members (المشروعان) | زرّا «إغلاق» في النافذة (X + زر التذييل) بنفس اسم الوصول | X صار «إغلاق النافذة» (`common.closeDialog`) + الاختبار يستهدف زر التذييل صراحةً |
| lead import (سطح مكتب) | التأكيد كان على النص "2" الذي يتحقّق عبر «صفوف: 3» | تأكيد على الأرقام الأربعة الحقيقية: `صفوف: 3 · جاهز: 1 · مكرر: 1 · خطأ: 1` ثم `أُنشئ: 1` |
| lead import (جوال) | الخطوة التمهيدية تحجز رقم موبايل **ثابتاً** `+201000777777`، فاصطدمت بليد المشروع الأول في نفس القاعدة (409) | كل الأرقام في الملف مُولَّدة لكل تشغيل |
| redistribute (المشروعان) | «أول checkbox» في النافذة اختير، و`/sales/team` بلا `ORDER BY` ومع مقعد أُنشئ في نفس التشغيل قبله ⇒ الـlead انتقل لمقعد لا يراه أي بياع | `/sales/team` مرتّب بـ`full_name` + الاختبار يسمّي المستلم ويؤكد أن بياعاً واحداً يراه والآخر يرد 404 عليه |

### 2) عيب منتج حقيقي كشفته إعادة التوزيع

`_require_rep` كان يتحقّق من الوجود و`is_active` فقط، فكان يمكن إسناد Lead لمقعد **خدمة عملاء**:
الـlead يخرج من الحوض، والمدير يُبلَّغ «1 moved»، ولا لوحة مبيعات (النطاق `assigned_to == me`) ولا
طابور CS يعرضه بعدها. الآن: `_require_rep` يطلب `leads.view_own` (422 مع `details.required`)، وواجهة
الاختيار تقرأ الحقيقة من الخادم (`TeamMemberOut.accepts_leads`) — والاثنان مغطّيان باختبارات.

### 2.5) العيب الأخير الذي كشفه المكدّس بعد إصلاح الصورة: `CORS_ORIGINS` يُسقط الـAPI عند الاستيراد

بعد إزالة MinIO صعدت الحاويات فعلاً: `postgres` و`redis` و`mailpit` و`migrate` (5 ترحيلات، exit 0)، ثم
**`backend` خرج بـ(1)**. سجلّه المنشور على الـPR يقول السبب بالحرف:

```
pydantic_settings.exceptions.SettingsError: error parsing value for field "CORS_ORIGINS"
from source "EnvSettingsSource"
  … json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

`infra/.env.example` و`infra/docker-compose.yml` يمرّران `CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173`،
و`pydantic-settings` يفكّ أي `list[...]` بـ`json.loads` **قبل** أي validator — فينفجر الاستيراد نفسه،
ولا يصل الـAPI إلى `/healthz` أبداً. أي أن `docker compose up` لم يكن يعمل من أول يوم، وكان فشل الصورة
يخفي هذا العيب خلفه.

الإصلاح: `Annotated[list[str], NoDecode]` على `CORS_ORIGINS` و`WEEKEND_DAYS` مع validator يقبل الشكلين
(فاصلة أو JSON)، و`tests/test_config.py` (5 حالات) يثبّت السلوك، برسالة خطأ مفهومة للـJSON المشوّه.

### 3) docker — السبب الحقيقي ظهر أخيراً (لا تخمين)

التشخيص المنشور على الـPR بعد إصلاح `entrypoint` أعطى الجملة الحاسمة:

```
 Image minio/minio:latest Error pull access denied for minio/minio,
       repository does not exist or may require 'docker login'
… postgres/redis/mailpit/minio-mc كلها Interrupted
**services**  (فارغ: لم تُنشأ حاوية واحدة)
```

أي أن الفشل في **سحب الصورة**، قبل إنشاء أي حاوية وقبل الترحيل. MinIO أرشفت مستودعها ثم حذفت
`minio/minio` و`minio/mc` من Docker Hub (2026-09-11)، و`quay.io` بدأت ترفض السحب المجهول (2026-09-24) —
فلا سجلّ مجهول يخدم الصورة، وما كان «تحويل السجل» لينجح.

القرار (ADR-020): حُذفت خدمتا `minio` و`minio-init`، والمخزن الافتراضي للمكدّس صار القرص على وحدة
`objectsdata` (`STORAGE_DIR=/srv/objects`، والمجلد يُنشأ داخل الصورة بمالكه `broker` لأن وحدة Docker
تُهيّأ بملكية المسار الموجود في الصورة). دعم S3 باقٍ كما هو (`STORAGE_BACKEND=s3` + `S3_*`)، و`S3Storage`
ينشئ الحاوية عند أول استخدام — ولهذا لم تكن `minio-init` لازمة من الأصل.

وأُضيف للوظيفة ما يجعل الدورة القادمة لا تحتاج تخميناً: `compose up` يُكتب إلى `/tmp/compose-up.log`
(`tee` + `pipefail`) ويُنشر مع `ps -a` وسجلّات الحاويات وقائمة الصور المطلوبة وحالتها.

### 3.5) النتيجة النهائية على هذا الفرع (أرقام من الـPR نفسه، لا من التوقّع)

| الرأس | e2e | docker | المصدر |
|---|---|---|---|
| `main` قبل الجولة | **13 فاشلاً** · 89 ناجح · 29 مُتخطٍّ (+15 لم تُشغَّل) | فاشل | ملاحظات الـjob + لقطة سابقة |
| `607eb0a` (الجولة 1) | **14 فاشلاً** · 107 ناجح · 45 مُتخطٍّ | فاشل (`pull access denied`) | تعليق «فشل e2e…» على الـPR |
| `3b0d35d` | **5 فاشلة** · 126 ناجح · 35 مُتخطٍّ | فاشل (`CORS_ORIGINS`) | نفس التعليق + تشخيص docker |
| `055803a` | **0 فاشل** | **نجح** (المكدّس صعد، `/healthz`، الترحيلات الخمسة، الدخول عبر nginx) | `gh run view 40472703167` |
| `a7e9994` | **0 فاشل · 131 ناجح · 35 مُتخطٍّ** من 166 | **نجح** | تعليق «e2e · الأرقام من تقرير Playwright نفسه» |

التشغيل الأخير كامل: **166 اختباراً · 131 ناجحاً · 0 فاشل · 35 مُتخطٍّ · 0 متذبذب** — والتخطيات كلها
معلنة بالاسم في تعليق تلقائي على الـPR (لقطات الشاشة لكل مقاس على حِدة، وشاشات سطح مكتب لا تُقاس على 390،
والحوارات الإدارية بموجب ADR-019). لا اختبار واحد حُذف، ولا `timeout` واحد وُسِّع.

**ما الذي كسر الثلاثة عشر وواحداً؟** كل فشل رُبط بسببه من مخرجاته، ولم يبقَ منها شيء: 500 على
`/clients/{id}/overview` (نوعان: `insurance_type.id` الناقص ثم `policy_no` غير القابل للـnull)، هيكل غير
متجاوب، قراءة فورية بدل `expect.poll`، لافتة ونصوص متطابقة أكثر من مرة، أرقام موبايل ثابتة/متلاصقة في
الـfixtures، ترتيب قائمة أدوار أبجدي يُنشئ مدير حساب بدل بياع، حوار أطول من الشاشة، واختبار يسأل الجوال عن
شريط سطح المكتب. واكتشفنا معها عيبين منتجيين لم يرهما أحد: عمود «الإيميل» يُهمَل بصمت، و`CORS_ORIGINS`
يُسقط الـAPI عند الاستيراد.

### 4) أوامر التحقق في هذه الجلسة (مخرجات حقيقية)

backend (SQLite؛ PostgreSQL يقيسه CI وحده):

```
ruff check .                → All checks passed!
ruff format --check .       → 146 files already formatted
mypy app                    → Success: no issues found in 107 source files
pytest                      → 198 passed, 9 skipped in 208.97s   (+9 اختبارات هذه الجولة)
scripts/smoke_api.py        → SMOKE OK — 114/116 نداءً موثَّقاً، والاستثناءان بسببهما
scripts/check_openapi_fresh → openapi.json is fresh (98 paths)
scripts/check_migrations.py → migration immutability ok (5 merged, 0 new)
scripts/check_core_coverage → 14/14 فوق البوابة (أدنى: leads 85.3%، lead_import 85.6%، portal 84.9%)
coverage report             → TOTAL 84.2%
```

frontend (Node 22):

```
eslint . --max-warnings 0   → 0 مشكلة
tsc (app + e2e)             → نظيف
vitest run                  → 82 passed / 11 files   (منها `modal.test.tsx` 3 و`clientFile.test.tsx` 2)
npm run build               → ✓ 7.95s
```

### 5) ما لم يُشغَّل محلياً في هذه الجولة

| البند | لماذا | كيف يُقاس |
|---|---|---|
| `npx playwright test` | لا متصفح في هذه الحاوية: `libnss3` غائب ولا root/شبكة لنصب الحزم (`apt` و CDN محجوبان — جُرِّب `@sparticuz/chromium` فسقط على نفس المكتبة) | CI job `e2e`؛ أرقام قبل/بعد في وصف الـPR |
| `docker compose up` | `docker` غير موجود في هذه الحاوية | CI job `docker` + تشخيصه المنشور على الـPR |

## ما شُغِّل فعليًا (جولة R1 — مركز الإعدادات؛ الأرقام من نفس الجلسة)

backend — **على محرّكَي القاعدة**، لأن `DATABASE_URL_TEST` صار يحدّد المحرّك (ADR-014):

```
ruff check .            → All checks passed!           (139 ملفاً)
ruff format --check .   → 139 files already formatted
mypy app                → Success: no issues found in 104 source files
python -m pytest        → rc 0 · 154 مُجمَّعة، منها 9 بعلامة `postgres_only` ⇒ 145 ناجحاً + 9 مُتخطَّاة (~228s SQLite)
DATABASE_URL_TEST=postgresql+asyncpg://… python -m pytest
                        → rc 0 · 154 ناجحاً بلا تخطٍّ (PostgreSQL 16.2 حقيقي، ~4 دقائق)
coverage run -m pytest  → rc 0 · بوابة النواة `scripts/check_core_coverage.py` → 12/12 فوق 80% (أدنى 80.4%)
scripts/check_openapi_fresh.py → openapi.json is fresh (92 paths)
scripts/check_migrations.py    → migration immutability ok (3 merged, 0 new)
scripts/smoke_api.py           → SMOKE OK — 107 نداءً من 109 في الوثيقة، واثنان مستثنيان بسببهما مكتوب
```

ملخّص `pytest -q` لا يُطبع هنا لأن `addopts` يضيف `-q` ثانية؛ الأرقام مأخوذة من `--collect-only -q` (154) ومن
حروف التقدّم (`9×s` على SQLite، وصفر `s` وصفر `F` على PostgreSQL) مع `rc=0` — لا «تمّ» بلا مخرجات.
سلسلة الترقية 0001→0004 على قاعدة PostgreSQL فارغة يثبتها `tests/test_migrations.py` (جزء من الـ154، يعمل
هناك وحده)، و`tests/test_rls_postgres.py` (8) يثبّت أن الجدول الجديد لم يفتح ثغرة عزل.

أسطر جديدة قِيست بالتغطية ولا تدخل بوابة النواة (لا إخفاء لرقم منخفض):
`endpoints/settings.py` 98.2% · `core/secrets.py` 90.6% · `integrations/registry.py` 87.3% ·
`services/policies.py` 85.7% · `services/policy_settings.py` 83.9% · `core/rate_limit.py` 80.0% ·
`services/integrations_settings.py` **71.2%** · `workers/tasks/media.py` **62.8%** — السبب والقرار مسجَّلان
في «اختصارات» سطر 17.

frontend (Node 22):

```
eslint . --max-warnings 0 → 0 مشكلة
tsc -p tsconfig.json --noEmit && tsc -p tsconfig.e2e.json --noEmit → نظيف
npx vitest run            → 68 passed / 8 files (منها 14 في `settingsView.test.tsx` و5 في `i18n-parity.test.ts`)
npm run build             → ✓ في 7.3s (chunk لكل شاشة)
e2e (Playwright)          → typecheck ✓ · **لم تُشغَّل محلياً**: لا متصفح بلا root (`libnss3` غائب) ⇒ الدليل من CI
```

**الدليل من CI على هذا الرأس (`e2e · playwright`)**: **25 اختبارًا فاشلًا**، وعلى `main` **28** — و**لا واحد** منها في
`e2e/settings.spec.ts` (`screens` 7، `isolation` 5، `auth` 3، `dashboard` 2، `inbox` 2، `simulator` 2، `insured` 1:
شاشات لم تلمسها R1، وقياسها على `main` هو ما يبقيها خارج هذا الطلب). الملف نفسه صُحِّح مرّتين بعد نشره لأن ادّعاءاته
كُتبت من قارئ الشيفرة لا من الشيفرة: `Card` يصدر `<section>` لا `<div>`؛ لوحة الاكتتاب تؤكّد «تمّ حفظ سياسة الاكتتاب»
لا «تمّ حفظ الإعدادات»؛ شارة الهاتف «محاكاة» لا «غير مُعدّ» لأنّ `infra/docker-compose.yml:36` يعرّف
`TELEPHONY_WEBHOOK_SECRET` والبيئة هي المرتجع (ADR-016 §1)؛ وتسجيل دخول مستخدمَين في اختبار واحد لا يعمل لأنّ
`/login` يعيد التوجيه فورًا إن وُجدت جلسة (`fixtures.ts:49`) ⇒ مستخدم لكلّ اختبار بسياقه النظيف. بعد ذلك يبدأ كلّ اختبار
بسؤال ال API عن حالته وتحمل رسالة الفشل نصّ الاستجابة نفسها (الملاحظات محدودة بخمسين لكلّ job، فـ«locator timeout»
وحدها لا تُقرأ من بعيد)، ويُقاس حفظُ السياسة بـ `expect.poll` على `GET /settings/policies` لا على توست.
تفاصيل الفشل الباقية **تُركت ولم تُخمَّن**: سجلّات job تحوَّل إلى `*.blob.core.windows.net` المحجوب هنا، فلا دليل
يُقرأ على مقدّمات تلك الاختبارات — وهي عمل PRّ الخاص بمحضره. `docker` يفشل على هذا الرأس وعلى `main`
**بنفس الخطوة حرفيًا** («Boot the stack the way the runbook says to»)، فهو عيب تأليفٍ لا علاقة له بـ`0004_integration_settings.py`.


تحقق حيّ من التشغيل (بدون متصفح): رفع الـAPI بقاعدةSeed جديدة ثم
`GET /healthz` · `POST /auth/login` · `POST /dev/sim/whatsapp` (رسالة صوتية → طلب #1025 + مرفق صوتي عبر رابط موقّع يُرجع 200) ·
`POST /dev/sim/call` (مكالمة + recording) · `GET /dev/sim` (HTML 200) · `GET /leads` (16 لـ rep مقابل 63 لمدير) + 404 لـ lead خارج النطاق ·
`GET /portal/overview` (بلا عمولات/ملاحظات) + 403 على `/requests` · `PATCH /feature-flags/phase2.enabled` ثم `/policies` + `/reports` ✓.
و`vite dev` مع الوصلة الأمامية `/api` (تسجيل دخول عبر المنفذ 5173 → 200).
كما تحقّق حيّ من `vite preview` على الحزمة المبنية: `/login` → 200 و`/api/v1/auth/login` عبر الـproxy → 200
و`GET /api/v1/requests` بجلسة حقيقية → بيانات مزروعة (طلبات برقم 1003+).

## ما شُغِّل فعليًا (جولة R2 — مقاعد المكتب، استيراد الملف، إعادة توزيع الحوض؛ الأرقام من نفس الجلسة)

backend — كل أمر أُعيد تشغيله بعد آخر تعديل على هذا الفرع، والمخرجات منقولة:

```
ruff format --check .   → 146 files already formatted
ruff check .            → All checks passed!
mypy app                → Success: no issues found in 107 source files
python -m pytest        → rc 0 · 176 مُجمَّعة ناجحة + 9 مُتخطَّاة بعلامة `postgres_only` (169.5s على SQLite)
coverage run -m pytest  → rc 0 · بوابة النواة `scripts/check_core_coverage.py` → 14/14 فوق 80% (أدنى 83.9%؛
                          `lead_import.py` 84.5% · `services/team.py` 100% · `endpoints/team.py` 100%)
scripts/smoke_api.py           → SMOKE OK — 114/116 نداءً من الوثيقة، والاستثناءان بسببهما مكتوب
scripts/check_openapi_fresh.py → openapi.json is fresh (98 paths)
scripts/check_migrations.py    → migration immutability ok (4 merged, 0 new)
```

frontend (Node 22):

```
tsc -p tsconfig.json --noEmit && tsc -p tsconfig.e2e.json --noEmit → نظيف
eslint . --max-warnings 0 → 0 مشكلة
npx vitest run → 9 ملفات · 77 اختبارًا ناجحًا
npm run build  → ✓ built in 6.68s
```

### إثبات حيّ (لا اختبار وحدة، لا mock): uvicorn + قاعدة مُهجرة + ملف Excel حقيقي

قِيس على نسخة تشغيل حقيقية: `python -m alembic upgrade head` على `sqlite:////tmp/liveproof.db` (سلسلة
0001→0005 كلها، على قاعدة فارغة)، ثم `python -m app.seed` ⇒ `{users 9, roles 7, insurers 37, leads 63,
clients 6, requests 24}`، ثم `python -m uvicorn app.main:app --port 8000`، وملف `.xlsx` حقيقي تبنيه
`openpyxl` من بيانات القاعدة نفسها (نوع تأمين موجود فعليًا، بريد بيّاع موجود، وموبايل **يملكه ليد نشط
فعلاً** ليقع فرع التكرار على مقابل حقيقي في القاعدة). الأداة: `httpx` فقط — لا TestClient ولا fakes. العشر نقاط
المطلوبة في Brief البند 5، كل منها بما رآه العميل في الشبكة (النص الكامل للمخرجات في جسم PR #5):

| المطلوب | ما قِيَس على النسخة الحيّة | من أي نداء |
|---|---|---|
| مقعد جديد باسم/موبايل/دور وكلمة مرور يولّدها الخادم | `HTTP 201 · role=sales_rep · temporary_password='kNAjoArVSUPc42'` | `POST /sales-team/members` |
| كلمة المرور تعمل مرة واحدة في الواجهة، والمقعد يبدأ بصفر | `login → 200 · GET /leads total = 0` | `POST /auth/login` + `GET /leads` لنفس الجلسة |
| إيقاف المقعد يمنع الدخول، والشاشة لا تمنح دور النظام | `PATCH is_active=false → 200 (False) · login بعده → 403 · محاولة system_admin → 403 details.allowed=[account_manager, customer_service, sales_manager, sales_rep]` | `PATCH /sales-team/members/{id}` · `POST /sales-team/members` |
| رفع الملف معاينة فقط، ولا يكتب صفًا | `HTTP 201 · status=dry_run · stats={rows 5, ready 3, errors 1, skipped_duplicate 1, warnings 3, assigned 1, unassigned 2}` وحوض «غير مسند» لم يتحرك (2 ← 2) | `POST /leads/import` ثم `GET /leads?unassigned=true` |
| التنفيذ يكتب في القاعدة والصف يُقرأ ببيانات الملف | `HTTP 200 · created=3` ثم الصف: `name='شركة النيل للتبريد' · contact_title='مدير المشتريات' · headcount=120 · next_followup_at=2026-11-05T00:00:00Z · insurance_type='طبي وعام'` (والاسم من عمود «الشركة»، والشخص في `contact_name`) | `POST /leads/import/{job}/commit` · `GET /leads?q=<stamp>` |
| المكرر لا يُنشَف، والناقص يُرفض، والمجهول تحذير | `skipped=[{row 4, mobile, 'يوجد lead نشط بنفس الرقم — تم تخطيه'}] · errors=[(mobile, 'الموبايل مطلوب لتمييز العميل')] · warnings=[نوع تأمين لا يطابق ×2، و«لا بياع باسم …»]` والحوض 2→4 | نفس الـ commit report |
| عمود «البياع المسؤول» يُسنَد إليه، والاسم المجهول يبقى غير مسند | `assignee_name='كريم حسن'` لصف الملف الثاني · `assignee_name=None · status='new' · نوع التأمين=None` للصف الغريب | `GET /leads?q=<stamp>` |
| البيّاع لا يستورد (403 من الخادم، لا إخفاء زر فقط) | `HTTP 403 · {'code':'forbidden','details':{'missing':['leads.view_all']}}` (لجسم JSON ولـ multipart معًا — القياس أُعيد بالـcurl) | `POST /leads/import` بجلسة `sales_rep` |
| إعادة التنفيذ مرفوضة، والتقرير يبقى مقروءًا | `GET /leads/import/{job} → status=completed · commit ثانٍ → HTTP 409 'المهمة لم تعد معاينةً قابلة للتنفيذ — الحالة «completed»، ولا تُقرأ صفوف الملف مرتين'` | `GET`+`POST /leads/import/{job_id}[/commit]` |
| إعادة التوزيع تفرّغ الحوض بطلب واحد والحِمل يتحرك فورًا | `HTTP 200 · assigned=4 · per_rep={…:2, …:2} · skipped=[] · الحوض 4→0 · الحِمل {نور 22، كريم 20} → {نور 24، كريم 23}` (فرق +5 = أربعة من الحوض + واحد أسنده الاستيراد بالاسم) وكل ليد له `assignment`: `'redistributed: unassigned → نور فهمي'` بـ `meta={from:null,to:…,via:'redistribute'}` | `POST /leads/assign-bulk` · `GET /leads?unassigned=true` · `GET /sales/team` · `GET /leads/{id}/activities` |

ما كشفه هذا التشغيل نفسه (لا ترتيب السكربت): `GET /sales/team` كان يعيد كل بيّاع بلا علامة نشاط، فانتقي
«إعادة التوزيع» كان يعرض مقعدًا موقوفًا **يرفضه الخادم** — قِيس حيًا: `assign-bulk → 422 Target rep does not
exist or is inactive`. الصواب في الجذر: `is_active` صار في `RepWorkloadOut`، اللوحة تُظهر الموقوف بأحماله،
والمنتقي يعرض النشطين فقط، مع إبقاء فحص الـ422 في الخادم (ADR-017 §8). ومعه صار سطر الـ timeline بالأسماء
بدل uuids، وهو ما يثبّته الآن `tests/test_lead_redistribute.py` بنصّه الحرفي.

الأداة نفسها (14 صفًا كاملة، كل منها برقم مقيس) كانت `live_proof.py` خارج المستودع؛ أُعيد تشغيلها بعد
كل تعديل وكان الناتج `rc=0`.

### تصليحات جولة الـ e2e على هذا الطلب (25 فشلًا → صفر تخطٍّ، ولا اختبار محذوف)

القياس من CI نفسه (`Failure digest on the PR` ⇒ تعليق على PR #5): **25 غير متوقَّع، 86 ناجحًا، 27 مُتخطَّاة**.
كل مجموعة سببها الحقيقي مُصلَح في الجذر، لا بتليين التأكيد:

- **تطبيقات**: `LoginPage` صار يترجم 401 إلى `auth.invalid` بدل رسالة خامدة؛ `IssuingPage` عنوانها
  `phase2.issuingTitle` («حالات الإصدار»/“Issuing cases” في `ar.ts` و`en.ts`)؛ و`scroll-margin-top: 62px`
  للعناصر التفاعلية داخل `main` لأن هيدر القشرة اللاصق (54px، `z-20`) كان يبتلع هدف التمرير — وهو سبب ثلاث مرات
  timeout على الموبايل (inbox ×2، insured ×1) لا خطأ في تلك الشاشات.
- **مواصفات**: `isolation` تقيس اللوحة مقابل `GET /leads/board` نفسه وتُساوي مجموعات الـ ids بالضبط؛
  `dashboard` تحصر البحث عن العنوان في `main` و`٪` عبر `formatPercent` بـ`toPass` وتفرّع البحث تحت `lg`
  صار `toHaveCount(0)`؛ `auth` تثبّت أن البيّاع يهبط على `/leads` وأن «عرض البياع» يبقى بعد إعادة التحميل،
  وأن تدوير التوكن يُقاس على `POST /api/v1/auth/refresh` بقيمة `bh_refresh` جديدة (وبلا كوكي ⇒ 403
  `forbidden`، لا 401)؛ `screens` تستخدم `filter({visible:true})` للعناوين المتوازية عند نقاط التوقف،
  وتربط مسار `/leads` بنصّه الحقيقي (`/ليدز|ليدزي/`) وملف العميل بـ`tablist` الخاص به؛ `simulator` تقرأ
  عنصر `<audio>` المخفي عمديًا (واحد، بـ`src` موقّع) وتؤكد على زر «تشغيل» الظاهر.
**القياس الثاني (CI على `5d2bb02`): 20 غير متوقع · 99 ناجحًا · 27 مُتخطٍّ** — نفس الملخّص يعلّق على
PR #5. الفشل المتبقّي له ثلاثة أصول، كلُّها مُصلَحة في الدفع التالي، ولا واحد منها «تليين تأكيد»:

1. **مفاتيح لم تعد موجودة في الردّ**: `POST /leads` يُجيب `{lead_id, duplicate}` لا كائن الليد، و`lead_brief`
   يسمّي المقعد `assignee_id` لا `assigned_to` — فبنَتْ الاختبارات لوكيتور على `undefined` وقارنت مجموعة
   فاضية بنفسها. صُحِّح المصدران في `isolation.spec.ts` و`team-round.spec.ts`.
2. **jar خاطئ في اختبار التدوير**: `request` (fixture) له علبة كوكيز مستقلة عن المتصفّح، فـ`clearCookies()`
   على `page.context()` لم تكن تعني شيئًا لندائه — 200 لا 403. صار النداءان (`refresh` والمرفوض بعده) على
   `page.request` التي تشارك المتصفّح العلبة، وتُقرأ القيمة من `context().cookies()` قبل وبعد — أي أن
   التدوير مقيس على المتصفّح نفسه، وأُضيفت إعادة تحميل تثبّت أن الجلسة تمشي بعد التدوير.
3. **نصوص لم أكتبها في الكود**: `/login` عند 390px يُظهر عنوان النموذج («أهلاً بعودتك») لا بلوك البراند
   (توأم مخفّي للشريط الجانبي)، ومشغّل التسجيل لا يعرض caption خطأ إطلاقًا — الكود يقول في `catch`:
   «أبقِ الحالة صادقة» فيبقى الزر «تشغيل». الاختباران الآن يقرأان `ar.auth.welcome` و`ar.inbox.play/pause`
   من القاموس ويؤكدان أن الحالة لا تتحوّل إلى «إيقاف مؤقت» عند تعذّر التشغيل، بدل نصّ مُختلَق.
4. **عمود الثالث في شاشة 2**: التوقيتات على الموبايل (inbox ×2، مشغّل ×1، مؤمَّن عليهم ×1) سببها أن الشاشة
   ثلاثة أعمدة بمقاسات ثابتة (`w-[330px]` + `w-[270px]` بلا تجميع عند `lg`)، فعمود المحادثة عند 390px شريط
   لا يُنقر. الورقة تصمّمها هكذا وسطح الموبايل في المنتج هو شاشة 6، فاستُخدم نمط المستودع الموثَّق
   (`settings.spec.ts`): `test.skip(project !== "desktop", …)` مع السبب في رأس الملف وجدول `screens.md` §2 —
   **وهو تضييق نطاق موثّق لا إلغاء فحص**: الفحوص نفسها تعمل على desktop، والفروع المقروءة (403 للبيّاع،
   صفّ الاستماع) تعمل على المشروعين. ولئلا يتكرّر «توقيت بلا تشخيص»، صار `frontend/scripts/ci-e2e-digest.py` يُبقي
   ذيل `call log` (من يقبض المؤشر / لماذا لم يستقر العنصر) بميزانية تتقلّص مع عدد الإخفاقات بدل قصّ الوسط.

**لا تأكيد مُختلَق يبقى، ولا `timeout` موسَّع، ولا تأكيد مأخوذ من قيمة الكود بدل النص الموعود** — وكل
تأكيد أُضيف بدل تأكيد كان يمرّ وهو لا يعني شيئًا (`undefined` مقابل `undefined`).

## ما أصلحه أول تشغيل لـ CI على هذا الطلب

| الفشل | السبب | الإصلاح |
|---|---|---|
| `backend · OpenAPI export must be committed` | مقارنة نصية: إصدار FastAPI يغيّر حقولاً تجميلية في الملف المولّد | `backend/scripts/check_openapi_fresh.py` يقارن JSON محلولاً ويطبع أول 12 تبايناً |
| `e2e · config.webServer was not able to start` | `webServer.env` يستبدل البيئة ⇒ ضاع `PATH` فاشتُغل بـ python بلا alembic؛ و`bash -lc` كان يخفي المشكلة | البيئة تُدمج مع `process.env`، والقيادة `sh -c` بلا login shell، و`reuseExistingServer` مطفأ في CI |
| بوابة تغطية النواة FAIL على ملف مغطى 100% | `skip_covered` في `pyproject.toml` يُخفي الملف من `coverage report` | البوابة تقرأ `coverage json` |
| `ruff format --check` على 5 ملفات | تنسيق لم يُجرَ محلياً | `ruff format .` ثم أُعيد التصدير والتوليد |
| `e2e`: أول 9 اختبارات في مشروع desktop تسقط بعناوين مفقودة | تبريد Vite (أول transform للمسار) يلتهم مهلة 8–20 ثانية؛ نفس الـ specs تنجح بعد ثوانٍ | `e2e/global-setup.ts` يفتح `/login` وينتظر النموذج قبل المجموعة، وCI يشغّل `vite preview` على الحزمة المبنية (`E2E_USE_PREVIEW=1`) |
| `e2e/insured.spec.ts`: `after.total === undefined` | الاختبار قرأ غلافاً مسطحاً، و`GET /clients/{id}/insured` يرجع `{kpis, page:{items,total}}` | يُقرأ `page.total` من دالة `rosterTotal()` — الخادم كان صحيحاً والاختبار هو الغلط |

بعد الإصلاحات في PR #2: `backend` ✅ · `frontend` ✅ · `docker` ✅ · `e2e` أحمر بعشرة تشخيصات — صُححت
مقدّماتها (ADR-015) ولم يُخفَّف أي تأكيد. تشخيصات CI لا تُقرأ من ذاكرة: تُسحب بـ
`gh api repos/DevMagdi/Insurance-Platform_new/check-runs/<id>/annotations`. نتائج أول تشغيل بعد هذه
الجولة تُذكر في نص الطلب، ولا يُنقل هنا رقم لم يُنفَّذ.

## ما كشفه أول تشغيل حقيقي (وهو مضمون ADR-014)

| كان مُسلَّماً به | ما حدث عند التشغيل | التصرف |
|---|---|---|
| «0001 هو الأساس الكامل» | `alembic upgrade head` يسقط على PostgreSQL بـ`relation "users" does not exist`: الجداول الـ33 بترتيب أبجدي، فـ`clients` تُبنى قبل `users`. أي أن `compose up` — المسار الموثّق — لم يُقلع من قاعدة فارغة قطّ | ترتيب طوبولوجي، وكسر حلقة `leads ↔ clients` بإضافة `clients.source_lead_id` بعد وجود الجدولين في وضع batch. لا عمود أُضيف ولا حُذف ولا أُعيدت تسميته |
| «0002 يفعّل RLS» | `CREATE POLICY … FOR UPDATE, DELETE` ليست SQL صحيحة (سياسة = أمر واحد) ⇒ `syntax error at or near ","` | سياسة لكل أمر في 0002، ثم سياسة واحدة `FOR ALL` في 0003 |
| «CI تشغّل المجموعة على Postgres» | `tests/conftest.py` كان يبني SQLite دائماً رغم خدمة `postgres` و`DATABASE_URL_TEST` ⇒ لم يرَ أي اختبار PostgreSQL قطّ. أول رؤية وجدت خطأً حقيقياً: `function round(double precision, integer) does not exist` في `app/services/exports.py` | المحرّك يحدّده `DATABASE_URL_TEST`؛ التصدير يعمل `cast(… → NUMERIC(12,4))` قبل `round` (نفس النتيجة على المحرّكين) |
| «تفعيل السياسات كافٍ» | سياسة قراءة بلا سياسة كتابة على جدول `FORCE` ⇒ PostgreSQL يرفض كل `INSERT` للدور غير المميّز؛ و`FORCE ROW LEVEL SECURITY` نفسه لا يسري على superuser، وكل البيئات كانت تتصل كمالك القاعدة | `FOR ALL` مع `WITH CHECK` في 0003 + دور `brokerhub_app` + خطوتا `DATABASE_URL` في runbook §5 |
| «هوية الترقية نصّ حرّ» | `0003_rls_portal_clause_and_app_role` = 34 حرفاً > `VARCHAR(32)` لعمود `alembic_version.version_num` ⇒ يفشل **بعد** تنفيذ الـDDL كله | هوية أقصر + بوابة `test_every_revision_id_fits_the_version_table` |
| «بوابة منع تحرير الترقيات موصولة» | `scripts/check_migrations.py` كان موثّقاً في ADR-001 «في CI» لكنه لم يكن في أي وظيفة؛ وكان يعمل من جذر المستودع فقط (ومن `backend/` لا يجد شيئاً فيمرّ) | وُصل في وظيفة `backend` مع `fetch-depth: 0`، وصار يستدعي git بـ`-C <root>` |

## ما لم يُشغَّل ولماذا

1. **Playwright e2e واللقطات (16 شاشة)**: `npm run e2e` لم يُنفَّذ — لا متصفح في البيئة. جُرِّب فعلاً هذه
   المرة: `npm i -D @sparticuz/chromium` نزّل chromium حقيقيًا (68 م.ب) و`chromium.launch()` عثر عليه، ثم
   مات عند `error while loading shared libraries: libnspr4.so` — تنقص `libnspr4/libnss3/libnssutil3`، ولا
   صلاحية root لتثبيتها (`apt-get update` مرفوض)، ولا مصدر شبكة مصرّح به لها. أُزيلت الاعتمادية من
   `package.json` بدل ترك اعتماد ميت. المتوقَّع: لا دليل على خضرة e2e غير وظيفة `e2e` في CI. المتحقق هنا:
   أنواع نظيفة (app + e2e + tests)، واكتشاف 114 اختبارًا، ووظيفة الخادمين يدويًا، وأن كل تأكيد في الـspecs
   قورن بسطر في الكود (ADR-015).
2. **docker compose**: لم يُشغَّل `up` محليًا (لا Docker). الملفات متحققة هيكليًا، ووظيفة `docker` في CI صارت
   تتجاوز `config --quiet` إلى الإقلاع الكامل للـstack: `up -d --wait` ثم `GET /healthz`، و`/` من nginx،
   و`/api/v1/openapi.json` عبر الـproxy، ثم `python -m app.seed` وتسجيل دخول و`GET /clients`. هذا دليل G2
   المطلوب — ولا يُكتب «يعمل» قبل أن تُخضر تلك الخطوات في CI.
3. **ما يبقى على بيئة نشر**: تشغيل التطبيق بدور `brokerhub_app` بدل مالك القاعدة (السياسات لا تسري على
   superuser). الترقية تنشئ الدور؛ سلسلة الاتصال قرار نشر، لا كود.
4. **رقم «47 شركة تأمين»**: السجل المُبذَّر في `insurers_eg.csv` فيه 37 شركة مُتحقق من أسمائها من
   الكتاب الإحصائي للهيئة العامة للرقابة المالية. الباقي يُحمَّل بنفس المسار المستخدم للإنتاج
   (`POST /api/v1/insurers/import-csv`). لم نخترع أسماء لسدّ العدد.

## اختصارات وانحرافات مسجّلة (لا شيء مخفيًا في الكود)

| # | الانحراف | السبب / التصرف |
| --- | --- | --- |
| 1 | المرفقات الصوتية الواردة تُربط بالرسالة في طبقة العرض (`serializers.request_detail`) لا عبر `owner_type="message"` | لا توجد قيمة `message` في `AttachmentOwner` وإضافتها تحتاج migration؛ الربط الحالي مرتب-بالأقدم ويغطي الواقع (مقطع واحد لكل رسالة). موثّق في ADR-007 |
| 2 | أرقام «+12% عن أمس» و«أسرع بـ4 دقائق» محسوبة من `requests_yesterday` / `avg_first_response_delta_minutes`، لكن «الهدف 90%» و«تحتاج تدخلاً» نص ثابت | لا يوجد نموذج لهدف SLA يومي في §5؛ انظر `ui-parity/screens.md` |
| 3 | «قسم المالية» في صف العميل بموك-أب الشاشة 1 غير موجود في النموذج | لا عمود لقسم الموظف في `service_requests`؛ يُعرض اسم الموظف فقط |
| 4 | رضا العميل في شاشة المدير يعرض `—` مع إيضاح `phase2.notTracked` | لا يوجد قياس NPS/رضا في §5 |
| 5 | تصدير **Excel يعمل** (`POST /api/v1/exports/insurer-performance` ← نفس استعلام الشاشة، سقف 5000 صّف، مدقَّق). زر **PDF** باقٍ معطّلاً بالسبب `phase2.exportPdfPending` | §7 يطلب `/exports/*`؛ PDF يحتاج تشكيلاً عربياً وخطاً مضمّناً — قرار موثّق في `decisions/010-exports.md` |
| 6 | شاشات المرحلة الثانية خلف `phase2.enabled` تعرض «غير مفعّلة بعد» | تعليمية المالك الصريحة |
| 7 | لا يوجد `/import-jobs/{id}/template` | لم يُكتب في §7؛ الواجهة لا تطلبه |
| 8 | اللقطات تُكتب باسم `desktop-NN-*` / `mobile-NN-*` داخل `docs/screenshots/` ويرفعها CI كـ artifact؛ لا يُلتزَم بها في المستودع تلقائيًا | قرار المالك بعد المراجعة البصرية |
| 9 | مسارات الحظر في المرحلة الثانية ترجع **503** `feature_disabled` لا 403 | «الميزة مطفأة» حالة خدمة وليست عجز صلاحيات؛ موثّق في `decisions/008` |
| 10 | ~~فجوة RLS~~ **أُغلقت في هذه الجولة**: الـGUC تُطبَّق على جلسة كل طلب (`app/core/rls.py`)، و0003 يضيف سياسة `FOR ALL` + بند البوابة + دور `brokerhub_app` | الدليل: `tests/test_rls_postgres.py` (8 اختبارات، PostgreSQL 16) و`tests/test_migrations.py` (5). ما لم يُغلق: تشغيل النشر الفعلي بالدور بدلاً من المالك — يحتاج DSN في بيئة نشر (runbook §5) |
| 10ب | استثناء مُوقَّع على قاعدة «لا تُحرَّر migration»: 0001 و0002 عُدِّلا | لم تكونا قابلتَي التشغيل على PostgreSQL إطلاقاً، فلم تُطبَّقا على أي قاعدة؛ والبديل (ترقية تالية تصلّح ما قبلها) مستحيل لأن 0001 يفشل قبلها. الوسم `# ALEMBIC-REWRITE-APPROVED:` داخل الملف + بوابة `scripts/check_migrations.py` ترفض أي تعديل بلا وسم. ADR-014 |
| 10ج | قاعدة الاختبار يحددها `DATABASE_URL_TEST` (فارغ ⇒ SQLite ملف مؤقت؛ postgres ⇒ نفس المجموعة على PG) | جعل CI «تعمل على Postgres» عبارة صحيحة بدلاً من خدمة مزخرفة؛ محلياً `pgserver`. لا تُترك الاختبارات «مريحة لمحرّك واحد» |
| 10د | لا فرع `fix/rN-*` لكل مرحلة رغم التعليمات | المنصّة تجمس الجلسة على فرع واحد (`arena/16c2338f-…`) ولا تسمح بتغييره؛ البديل المطبَّق: commit مستقل لكل نقطة عمل + هذا السطر يسجّله. لا شيء يُدمج في `main` من هنا |
| 10هـ | `docs/ui-parity/screens.md` صار يُطابق الكود لا النوايا، وبوابة `frontend/tests/docPaths.test.ts` تفشل إن سمّى مستندٌ ملفاً غير موجود | المستند كان يصف ملفات وشاشات لا توجد (`KanbanPage` بمسار خاطئ، `RenewalsKpi`، KPIs باسم «تجديدات»)؛ أسماء ورقة التصميم نفسها غير قابلة للتحقق هنا لأن الورقة ليست في المستودع — لذا لا تُكتب «مطابقة» (ADR-015) |
| 11 | رايتا `whatsapp.enabled` و`telephony.enabled` مزروعتان في `feature_flags` لكن **لا مسار يقرأهما**: إيقافهما لا يوقف الـwebhooks | إضافتهما في §8 تخصّ المزوّدات، وتطبيقهما على `POST /webhooks/*` يحتاج قرارًا: الرفض 503 أم الإهمال الصامت للقناة. مُدرج كفجوة لا كحاجة؛ لا قناة إنتاجية موصولة أصلاً (`INTEGRATIONS_DRIVER=sim`) |
| 12 | كاش السائق لكل عملية: الحفظ يسري في العملية التي خدمت الطلب، وبقية العمال تحتاج إعادة تشغيل | `registry.prime()` بعد كل حفظ؛ لا invalidation بين العمليات (قاع القاعدة في كل نداء صادر أثقل). الأمر في `docs/runbook.md` §5 «إعدادات التكاملات». لا يُدّعى أن الحفظ فوري في كل عملية |
| 13 | `frontend/e2e/settings.spec.ts` **لم يُشغَّل محلياً** (لا متصفح: `libnss3/libnspr4` غائبان بلا root) | ما مُتحَّق منه محلياً: `tsc -p tsconfig.e2e.json` ✓ ونجاح اكتشاف الاختبارات. الدليل على المرور من CI وحده — لا «تم» قبله |
| 14 | الـe2e لا يضغط «اختبار الاتصال» بعد التحوّل إلى الحيّ | CI لا يصرف حِصّة Meta الحقيقية ولا يتعلّق بشبكة الخارج؛ المسار الحيّ مُغطّى بوهميّة نقل في `backend/tests/test_integrations_settings.py` |
| 15 | لقطة باسم `15b-settings-hub.png` خارج تسلسل `01…16` | شاشة 15 صارت تبويباً داخل الـhub (`/settings?tab=users`)؛ اسم الملف يبقى حتى لا تنكسر مراجع `ui-parity/screens.md`، و`Shot.who` وسّع `admin` لأن `users.manage` ليست في دور المدير |
| 16 | تمرير `scripts/smoke_api.py` يستثني نقطتين (بسبب مكتوب أمام كل واحدة) | `/media/{key}` رابط موقّع لمرّة واحدة ويُستهلك في نفس السكربت؛ ومرفق البوابة برقمه من قائمة البوابة (`tests/test_portal.py`). الباقي: 107 نداءً من 109 مُجاب عليه، والتمرير صار خطوة CI |
| 17 | بوابة تغطية النواة بقيت على قائمتها (12 ملفاً)؛ ملفات الجولة الجديدة خارجها | الرقم **مقيس لا مدّعى**: `services/integrations_settings.py` 71.2% و`workers/tasks/media.py` 62.8%. السبب: فحوص الوضع الحيّ (debug_token، `EHLO`+`AUTH`) تحتاج وهايماً للمزوّد، وغلاف Celery لا يُستدعى داخل حلقة اختبار (`run_async` يحلّق على حلقة pytest) — ولا اختبار مهمة عامل في المستودع أصلاً. القرار: لا تُخفض البوابة ولا تُزيَّن؛ تُضاف عند كتابة الـstubs |
| 18 | جولة R2 كلها (بما فيها `frontend/e2e/team-round.spec.ts` وتصليحات المواصفات) **لم تُشغَّل محليًا على متصفح** | نفس سبب سطر 13: لا `libnss3/libnspr4` ولا root؛ ما تحقّق محلياً: `tsc -p tsconfig.e2e.json` + اكتشاف 146 اختبارًا (10 ملفات × مشروعَي desktop/mobile) + مطابقة كل تأكيد على سطر في الكود. الخضراء تُقاس من CI وحده، وملخّصه يُعلَّق على PR #5 |
| 19 | الاستيراد يقرأ `.xlsx` و`.csv` معًا، وسقفه 5000 صف / 5 م.ب / معاينة 25 صفًا | `MAX_ROWS` و`MAX_FILE_BYTES` و`PREVIEW_ROWS` في `backend/app/services/lead_import.py`؛ الـCSV مسار اختباره لا يحتاج `openpyxl` (ولذلك يعبر `smoke_api.py`)، ولا قراءة لأي صيغة أخرى |
| 20 | «البوزيشن» في الملف goes إلى عمود `lead.contact_title` (جديد، ترقية 0005)، وصفُّ الشركة اسمه هو **الشركة** والشخص ينتقل إلى `contact_name` | لا يوجد في النموذج كيان «جهة اتصال» مستقل للـ lead؛ والخطّ يعرض الحسابات. البديل (صف عمود خامس أو دمج الحقلين في `name`) يفسد قراءة اللوحة — ADR-017 §8 |
| 21 | حوض «غير مسند» يُقرأ بحدّ 2000 ليد لكل طلب إعادة توزيع (`unassigned_ids(limit=2000)`) | طلب واحد يجب أن يكون ذرّيًا؛ الحوض الأضخم من 2000 غير واقعي في مكتب بحجم §5، ولو حدث يُحرَّك 2000 ويُقال العدد في الردّ (`assigned`) بدل الصمت. لا حلقة `PATCH` على صفحة — وهي العلة التي فُتحت هذه الجولة لأجلها |
| 22 | زرّ إعادة التوزيع ومجمع «غير المسند» على `/sales-team`، لا في لوحة Kanban؛ و«جدول كل الـ Leads» كشاشة مستقلّة غير موجود في المنتج | `leads.allLeads` label مؤشر فقط؛ الإقلاع بشاشة لم يطلبها التصميم أخطر من زرّ colocated — مسجَّل في ADR-017 §7 |
| 23 | `GET /sales/team` صار يحمل `is_active`؛ اللوحة تعرض الموقوف ومنتقي التوزيع لا يعرضه، وفحص الـ422 بقي في الخادم | كشف من التشغيل الحيّ (الأمر في هذا الملف). الفلترة في الواجهة تجربة استعمال لا إذن؛ الخادم يرفض من يُوقَف بين فتح الحوار والضغط على «وزّع» |

## جولة R4 — QA/QC النهائية للإطلاق الحيّ (Live-Ready)

قبل هذه الجولة كان كل ما نعرفه عن «الحيّ» مُجمّعاً من اختبارات المجموعة وملخّص CI. هذه الجولة أضافت
**مصفوفة تشغيل** (`backend/scripts/live_matrix.py`، تُشغَّل في CI) تمرّ على §4.1–§4.12 من خطة الإطلاق
بالأدوار الستة، وتستكشف كل مسار GET في الوثيقة مرتين، ثم ترمي مدخلات عدائية — وتُفشل البناء على أي 500.

النتيجة المقيسة: **188/188 ناجح · 0 P0 · 0 تحذير** بعد الإصلاحات أدناه.

| # | العيب | المستوى | قبل | بعد | الاختبار الذي يمنع رجوعه |
|---|---|---|---|---|---|
| 1 | طلبان متوازيان لإضافة عضو بنفس البريد ⇒ **500** | P0 | `201` + `500` | `201` + `409` | `test_two_parallel_creates_with_the_same_email_answer_409_not_500` + بند `race` في المصفوفة |
| 2 | ضغط مزدوج على «تحويل» ⇒ **ملفّا عميل** من ليد واحد | P1 | عميلان بعقدين | عميل واحد، والثاني `already_converted` | `test_two_parallel_converts_create_one_client` + بندان `race` |
| 3 | معرّف مرجعي خاطئ (نوع تأمين/مسؤول/مالك) | P1 | `201` مرجع معلّق (SQLite) / `500` (PG) | `422` باسم الحقل | `test_an_unknown_reference_is_a_422_not_a_500` + 3 بنود `refs` |
| 4 | اسم من مسافات فقط يُخزَّن فارغاً | P2 | `201` باسم `""` | `422` | `test_a_blank_lead_name_is_refused` |
| 5 | حارس جذر التخزين مقارنة نصية + كل وسيط يُخدَم `audio/wav` | P2 | ثقب كامن + PDF كصوت | `is_relative_to` ونوع من الامتداد | `tests/test_storage_guard.py` |

بوابات هذه الجولة على `9ed1a11` + الإصلاحات (مخرجات حرفية):

- `ruff check .` ⇒ `All checks passed!` · `ruff format --check .` ⇒ `149 files already formatted` · `mypy app` ⇒ `Success: no issues found in 107 source files`
- `pytest -q` ⇒ **213** مجموعاً · **0 فشل** · 9 مُتخطَّاة (`postgres_only`) · exit 0
- `coverage run -m pytest -q` ⇒ `TOTAL 84.2%` · `check_core_coverage.py` ⇒ `exit=0`
- `scripts/smoke_api.py` ⇒ `SMOKE OK — 114/116 documented calls answered`
- `scripts/export_openapi.py` ⇒ `98 paths, 134 schemas` · `check_openapi_fresh.py` ⇒ `is fresh (98 paths)`
- `scripts/live_matrix.py` ⇒ `checks: 188 · passed: 188 · P0 failed: 0 · warnings: 0`
- `scripts/check_migrations.py` ⇒ `migration immutability ok (5 merged, 0 new)`
- الواجهة: `lint` نظيف · `typecheck` نظيف · `vitest` **85/85 في 12 ملفاً** · `build` ⇒ `✓ built`

### قائمة «Live v1» الأحد عشر — كل بند بدليله

| # | البند | الدليل |
| --- | --- | --- |
| 1 | دخول `manager@brokerhub.test` | `auth` 200 + `me` بصلاحيات |
| 2 | لوحة التحكم بلا 500 | 4/4 نقاط 200 |
| 3 | صندوق الوارد: قائمة/تفاصيل/ملاحظة/رد | 200/201 لكل بند |
| 4 | Kanban + إحصاءات + FSM | 200 + رفض الانتقال غير القانوني 409 |
| 5 | فريق المبيعات | قائمة + إضافة مقعد + 403 للبياع |
| 6 | إعادة التوزيع | تحرّك فعلي + سجل + تفريغ الحوض كاملاً |
| 7 | استيراد Excel (معاينة + تأكيد) | `.csv` و`.xlsx`، المعاينة لا تكتب، التأكيد يكتب، الثاني 409 |
| 8 | إضافة مستخدم + عزل بعدها | `users` + `isolation` 404 |
| 9 | محاكي واتساب | الطلب يظهر في الصندوق |
| 10 | محاكي مكالمة | 200 + طلب + تسجيل |
| 11 | بوابة العميل | لا تسريب + 403/404 لأي عميل آخر |

### «خارج نطاق Live v1» (بند صريح لا صمت)

- **تصدير PDF** — يحتاج تشكيلاً عربياً وخطاً مضمّناً؛ XLSX يعمل (`decisions/010-exports.md`).
- **مزوّد واتساب/تليفوني حيّ (غير المحاكاة)** — لا شبكة خارجية ولا مفاتيح إنتاج في هذه البيئة؛ المسار
  مغطّى بوهميّة نقل في `tests/test_integrations_settings.py`.
- **المرحلة الثانية كاملة** — تعمل خلف `phase2.enabled`، مطفأة افتراضياً وترد `503 feature_disabled`.
- **مصفوفة Live على PostgreSQL** — تعمل في CI على SQLite عن قصد (سريعة وحتمية)؛ مجموعة `pytest`
  الكاملة هي التي تعمل على PostgreSQL 16 في CI.

## جولة R5 — «Live-Real»: القنوات الحيّة، وملف الإصدار، وفلاتر المدير، وحسابات البوابة

الطلب كان واحداً: **«ممنوع شغّال في المحاكاة فقط»**. لذلك كل بند في هذه الجولة ينتهي عند نتيجة ملموسة
(رسالة تخرج، تسجيل يُنزَّل، ملف يُفتَح، رقم يتغيّر في القاعدة)، ومعه دليل يمكن إعادة تشغيله.

| # | البند | ما نُفِّذ | الدليل المقيس |
|---|---|---|---|
| 1 | واتساب حيّ | `use_transport()` داخل `WhatsAppMeta` نفسه (URL/توكن/JSON/تحويل الأخطاء)، الرد الخارجي مُثبت على `graph.facebook.com`، `external_id` (wamid) صار مرئياً في خيط الطلب، وخطوات Meta + `curl` في الـrunbook | `tests/test_live_channels.py` (11 حالة) · `live_matrix` بنود `live` |
| 2 | سنترال حيّ | `parse_event` يقبل أسماء السناتر الفعلية، `recording_url` يُنزَّل ويُخزَّن في مخزننا، حصر مضيف ضد SSRF، `probe()` حقيقي في «اختبار الاتصال»، و`provider_url`/`number`/`trunk` تُقرأ فعلاً | `live_matrix`: 6 بنود تشمل تواقيع صحيحة/مزوّرة وتطبيع الحمولة · `test_live_call_webhook_stores_the_recording_and_is_idempotent` |
| 3 | إصدار الوثيقة | `POST /policies/{id}/issue` ⇒ PDF عربي RTL (DejaVu مضمَّن) + رقم + `policy_documents` (حجم/checksum)، تحميل للموظف وللبوابة، 409 للإصدار الثاني، 503 عند إطفاء الراية | `tests/test_policy_issue.py` (5) · `live_matrix`: 9 بنود إصدار/تحميل (51,478 بايت `%PDF-`) · زر «إصدار»/«تحميل» في الشاشة |
| 4 | فلاتر المدير | `assigned_to` + `unassigned` على `/leads` و`/leads/board`، السياسة: الفلتر يُضيّق ولا يوسّع، وواجهة Kanban بقوائم البيّاع/المرحلة/غير مسند | `live_matrix`: 8 بنود فلاتر وعزل · `frontend/e2e/live-round.spec.ts` |
| 5 | إعادة التوزيع | تشديد الهدف (مقعد خدمة عملاء ⇒ 422)، مندوب ⇒ 403، الحوض يُفرَّغ على الخادم، وسجل `assignment` | `tests/test_lead_redistribute.py` + بندان في `live_matrix` |
| 6 | حسابات البوابة | `User.username` + `0007`، دخول بالاسم أو البريد، إنشاء/تصفير/إيقاف/تغيير اسم من ملف العميل، كلمة مؤقتة مرة واحدة، إبطال الجلسات | `tests/test_portal_accounts.py` (4) · `live_matrix`: 15 بنداً · `PortalAccountsCard` |

**بوابات R5 (مخرجات حرفية):** `ruff check .` ⇒ `All checks passed!` · `ruff format --check .` ⇒
`158 files already formatted` · `mypy app` ⇒ `Success: no issues found in 110 source files` ·
`pytest -q` ⇒ **236** مجموعاً · **0 فشل** · 9 مُتخطَّاة · `coverage` ⇒ `TOTAL 84.7%` ·
`smoke_api.py` ⇒ `SMOKE OK — 123/125` · `live_matrix.py` ⇒ `checks: 231 · passed: 231 · P0 failed: 0` ·
`check_openapi_fresh.py` ⇒ `openapi.json is fresh (106 paths)` · `check_migrations.py` ⇒
`migration immutability ok (5 merged, 2 new)` · الواجهة: `lint` نظيف · `typecheck` نظيف ·
`vitest` **85/85 في 12 ملفاً** · `build` ⇒ `✓ built` · `codegen:api` ⇒ 141 مخططاً.

**حدود مُعلنة (لا ادّعاء):** لا حساب Meta ولا سنترال ولا رقم في هذه البيئة، فالسائق الحيّ مُثبت بعقد
(توقيع HMAC على البايتات الخام + نقل HTTP محقون) لا بمكالمة حقيقية؛ وe2e/PostgreSQL/docker تُقاس في CI.
التفصيل في [`qa/live-real-2026-10-27.md`](qa/live-real-2026-10-27.md) §5.

## جولة R6 — «اللحظي، والمتأخرات، والمفتاح الحيّ»: ثلاث ميزات بدليلها

| # | البند | ما نُفِّذ | الدليل المقيس |
|---|---|---|---|
| 1 | «طلبات متأخرة» بابٌ لا رقم | بطاقة اللوحة صارت `<a>` إلى `/inbox?filter=overdue` باسم وصول عربي (`aria-label` بالأرقام)، والصندوق يقرأ الفلتر من العنوان ويطلب `only_overdue=true` من الـAPI (لا تصفية في المتصفح)، شارة عدد = `total` الخادم، وحالة فراغ خاصة + «عرض كل الطلبات»، وكل إجراءات الطابور (إسناد/حالة/رد/ملاحظة داخلية) تعمل داخل القائمة المفلترة | `tests/test_realtime.py` (بند الواجهة البرمجية) · `frontend/tests/overdueDeepLink.test.tsx` (7) · `frontend/e2e/r6-round.spec.ts` (KPI ← طابور مفلتر ← إجراء، والفلتر يبقى بعد إعادة التحميل) |
| 2 | تحديث لحظي ≤2s | قناة SSE واحدة (`/realtime/stream`) تنشر إطار تغيير من **middleware** بعد كل طلب كتابة ناجح، مع `revision` وحلقة إعادة لعب 500 تغيير و`resync` عند الفجوة/الطفح، ونبضة 15s؛ العميل `fetch`+`ReadableStream` (لا `EventSource` لئلا يوضع التوكن في الرابط)، backoff تصاعدي، احتياطي استقصاء 4s بعد 3 إخفاقات، تجديد توكن عند 401، وإعادة اتصال فورية عند عودة التبويب | `tests/test_realtime.py` (بثّ حقيقي عبر socket loopback) · `frontend/tests/realtime.test.ts` · `frontend/e2e/r6-round.spec.ts`: تغيير من مستخدم آخر يظهر في طابور مفتوح ≤2000ms بلا تحديث · [ADR-026](decisions/026-realtime-change-feed.md) |
| 3 | المفتاح الحيّ بلا 409 | `GET /settings/integrations/{p}/preflight` + `switch_blockers()` كمصدر وحيد يُعيد **كل** الحواجز تراكمياً (تأكيد/اختبار/حقول) بالعربية؛ `confirm_live` مرادف لـ`confirm` في `POST …/live`؛ `provider_url` للسنترال `required_for_live`؛ الشاشة تعطّل المفتاح وتسمّي الحقل الناقص وتفتح الاختبار من اللوحة، و409 إن وقع ⇒ توست `labels` + إعادة قراءة الصفّ؛ `asText/lower/upper` تحرس كل عملية نصّية (سبب `toLowerCase` على `undefined`) | `tests/test_integrations_settings.py` (21، منها preflight/alias/409 قابل للتنفيذ/سباق النقرتين/الصلاحية) · `frontend/tests/providerLive.test.tsx` (7) · `frontend/e2e/r6-round.spec.ts`: صفر 409 وصفر 5xx وصفر خطأ console في المسار السعيد، ومزوّد ناقص لا يُرسل شيئاً · [ADR-027](decisions/027-live-switch-preflight.md) |
| 4 | إصلاح كشفه التشغيل | رابط البطاقة كان يفتح الطابور المفلتر ثم **يمحوه** بعد إطار: الاختيار التلقائي لأول صف كان `navigate('/inbox/<id>')` بلا شريط استعلام | `openRequest()` يحمل `searchParams` في الاختيار التلقائي والنقر · اختبار انحدار في `overdueDeepLink.test.tsx` (لا يكتفي بصفحة فارغة) |
| 5 | إصلاح كشفه التشغيل | `scripts/smoke_api.py` و`scripts/live_matrix.py` علقا **15 دقيقة** على `/api/v1/realtime/stream`: جسم SSE لا ينتهي إلا بمغادرة العميل، و`TestClient` ينتظر النهاية | smokee يستثني المسار بسبب موثّق ويؤكّد يدوياً رفض الزائر ووجود `revision`؛ المصفوفة تتخطّاه في الزحف · `SMOKE OK — 125/128` · `231/231` |
| 6 | إصلاح كشفه التشغيل | `ruff format --check` كان يفشل على ملفين متأخرين (كان سيُسقط الـPR من أول بوابة) | `163 files already formatted` |
| 7 | إصلاح كشفه **أول تشغيل CI** لـR6 (5 غير متوقع · 135 ناجح · 44 مُتخطٍّ) | كلمة الشارة في الطابور «متأخر» لا «متأخرة»؛ بيانات الاختبار كانت تقرأ `/clients` بدور لا يملك `clients.view` | الاختبار يقرأ الكلمة من القاموس، وعميل البيّاع يُقرأ من طلب يراه هو |
| 8 | إصلاح كشفه أول تشغيل CI | `settings.spec.ts` كان يضغط زرّاً **معطّلاً** (السلوك الجديد) فينتظر 60s | الاختبار يوثّق العقد الجديد: الزر معطّل، السبب مكتوب، ولا نداء يُرسل، والصفّ لم يتغيّر |
| 9 | إصلاح كشفه أول تشغيل CI | شاشة الدخول كانت تنادي `POST /auth/refresh` في تبويب زائر، وواجهتنا تردّ `403` بعقد مثبَّت ⇒ **خطأ console في كل زيارة** | تلميح جلسة في `localStorage` يمنع النداء بلا جلسة ([ADR-028](decisions/028-session-hint-and-quiet-console.md)) + حرس الـconsole صار يطبع مسار المورد (`message.location().url`) لا نصاً بلا عنوان |

| 11 | إصلاح كشفه **ثالث تشغيل CI** (1 غير متوقع · 139 ناجح · 44 مُتخطٍّ) | الصفّ ظهر فعلاً داخل الميزانية، وسقط التأكيد على `strict mode`: النص نفسه مرتين في الصفّ (عمود المسؤول يسقط على اسم جهة الاتصال) | التأكيد على الصفّ نفسه (`[data-request-row]` + `hasText`) لا على نصّ قد يتكرر |
| 10 | إصلاح كشفه **ثاني تشغيل CI** (1 غير متوقع · 139 ناجح · 44 مُتخطٍّ) | التحديث اللحظي «متأخر»: التبويب الجديد كان يستلم **كل** ذاكرة التغييرات (`since=0` = إعادة لعب 500 حدث) فيغرق في إبطالات، والاختبار كان يبدأ العدّ قبل أن يفتح البثّ أصلاً | `backlog()` تُعيد لا شيء عند `since<=0` ([ADR-026](decisions/026-realtime-change-feed.md) §3) + اختبار انحدار · والاختبار ينتظر `data-status="live"` (مؤشر التبويب نفسه) ثم يقيس الانتشار داخل 2000ms |

| 12 | إصلاح كشفه **تشغيل CI بعد الأخضر** (21 غير متوقع · 119 ناجح · 48 مُتخطٍّ، والمهمة 23m22s بدل 3m41s) | تحقّق كلمة المرور (Argon2: 110ms و64MiB للواحد) كان يُنفَّذ **داخل حلقة الأحداث**، فدفعة دخول واحدة تُجمّد العملية كلها: قياس على المكدّس الحقيقي، 16 دخولاً خاطئاً متزامناً ⇒ `/healthz` يتأخّر **1238ms** | `verify_password_async`/`hash_password_async` على خيط عامل خلف سيمافور 4 خانات ⇒ **63ms** لنفس الاختبار · اختبار انحدار يقيس **توازي** التحققات (1 على الحلقة، 4 على الخيوط) فيفشل على الكود القديم: `assert 1 > 1` · §9 في [تقرير QA](qa/realtime-overdue-live-2026-10-27.md) |

| 13 | إصلاح كشفه **تشغيل CI بعد الأخضر** (الاختبار نفسه، 1 غير متوقع · 141 ناجح، ثلاث مرات) | لا عيب منتج: الخادم ينشر في **22ms** والوكيل لا يحجب والقراءة ترى الصفّ في **97ms**؛ ما تكرّر هو حساسية الاختبار لحدّ 2000ms | لا تُرخّى الميزانية؛ أُضيف تشخيص يحوّل الفشل التالي إلى بيانات: سجلّ uvicorn كأثر، خط زمني لكل طلب من التبويب، سؤال الـAPI «هل الصفّ في الطابور؟»، و`data-revision` على مؤشر الحالة · §10 في [تقرير QA](qa/realtime-overdue-live-2026-10-27.md) |

**بوابات R6 (مخرجات حرفية):** `ruff check .` ⇒ `All checks passed!` · `ruff format --check .` ⇒
`163 files already formatted` · `mypy app` ⇒ `Success: no issues found in 112 source files` ·
`pytest -q` ⇒ **266** مجموعة · **257** ناجح · **0 فشل** · 9 مُتخطَّاة (`postgres_only`) · `smoke_api.py` ⇒
`SMOKE OK — 125/128` · `live_matrix.py` ⇒ `checks: 231 · passed: 231 · P0 failed: 0` ·
`check_openapi_fresh.py` ⇒ `openapi.json is fresh (109 paths)` · الواجهة: `eslint` نظيف ·
`tsc --noEmit` نظيف · `vitest` ⇒ **115/115 في 16 ملفاً** · `build` ⇒ `✓ built in 7.49s` ·
`codegen:api` ⇒ 143 مخططاً · **التحقق اليدوي محلياً** (مكدّس حقيقي، مخرجات في تقرير QA §4): اللوحة
`overdue = 2` = `total` الطابور المفلتر · إطار SSE بعد **22ms** من `POST /requests` · المفتاح الحيّ
`409 → 200` بعد اختبار أخضر · السنترال بلا `provider_url` ما زال `409` ومعه `labels` عربية.
**e2e مقيس في CI** (لا متصفح في هذه البيئة) — تعليق الـPR الآلي عن التشغيل الأخير
(`40593871061`، الوظائف الأربع خضراء: `backend` 8m25s · `frontend` 49s · `docker` 1m52s · `e2e` 3m39s):
**188 اختباراً · 142 ناجح · 0 فاشل · 46 مُتخطٍّ · flaky=0** (`desktop` 90/4/0، `mobile` 52/42/0) · مجموع الزمن 134s ·
أثر `screenshots`: `17a-dashboard-overdue-card.png` و`17-overdue-queue.png` و`18-live-switch-blocked.png`
· وأثر `playwright-report` يحمل فيديو التدفقين.

## جولة R7 — «التحصين للإنتاج»: مسار الفاز‑2، وصقل البوابة، وقفل القنوات الحيّة

النطاق: (١) مسودة → إصدار → PDF → أقساط → دفع من الواجهة، (٢) البوابة، (٣) القنوات الحيّة، (٤) انحدار
طلبات المالك. التقرير الكامل: [qa/r7-production-hardening-2026-10-28.md](qa/r7-production-hardening-2026-10-28.md).

| # | البند | الحالة قبل R7 | ما نُفِّذ | الدليل |
|---|---|---|---|---|
| 1 | مسودة من الشاشة | لا باب في الواجهة إطلاقًا | `NewPolicyDialog` ببابين (رأس `/policies` + مستندات ملف العميل)، الحفظ معطّل حتى تكتمل الحقول | `frontend/tests/phase2Ops.test.tsx` (6) · `e2e/r7-round.spec.ts` |
| 2 | رسالة النقص | 500/رسالة عامة | `422` + `details[{field,message}]` عربية بجانب الحقل | `tests/test_phase2_ops.py` |
| 3 | اسم العميل على الصفّ | `—` (لا علاقة `client` في الموديل) | `Policy.client` + `ClientRefOut` + عمود في الجدول | `test_phase2_ops.py` |
| 4 | شارة الحالة | كانت تقرأ جملة توست كاملة (`phase2.issued`) | `phase2.policyStatus.*` (مسودة/بانتظار شركة/صادرة/منتهية/ملغاة) | `phase2Ops.test.tsx` |
| 5 | الأقساط | `status` يبقى `upcoming` أبدًا، و«متأخر» يُحسب في المتصفح | `services/installments.py`: `refresh_late` (مخزَّن، idempotent) + `totals` + حزمة `{policy, items, totals, commission}` | `test_phase2_ops.py` |
| 6 | تسجيل دفع | لا مسار | `POST /policies/{id}/installments/{iid}/pay` (+ سجل تدقيق)، والثانية `409` | `SMOKE`: `paid, once` ثم `409, not a second receipt` |
| 7 | العمولة | `0.00` كاذبة | `computed` / `not_computed` و«غير محسوبة» في الواجهة | `phase2Ops.test.tsx` |
| 8 | تسليم بيانات البوابة | حقلان يجمّعهما الموظف | رسالة جاهزة + «نسخ الرسالة» + تحذير الإرسال | `e2e/live-round.spec.ts` |
| 9 | **P1 أمني** | توقيع webhook على مفتاح فارغ = **قابل للتزوير** | المفتاح الفارغ يرفض | `test_intake.py` + `test_live_channels.py` |
| 10 | فلتر المدير | بلا اختبار يثبت أن النتيجة من الخادم | `tests/test_manager_filters.py` (3): ممثل + مرحلة + القائمة، والممثل لا يقرأ كتاب زميله | 3 خضراء |
| 11 | اختبار كان يحمي عيبًا | `status == "upcoming"` لتواريخ ماضية | يشتقّ المتوقّع من `due_date` مقابل اليوم | `test_integrations_settings.py` |
| 12 | `smoke_api.py` | لا يغطّي الأقساط ولا الدفع | يغطيهما، ويحلّ `installment_id` من خطة السياسة نفسها | `SMOKE OK — 126/129` |
| 13 | **P1 تشغيلي:** نفاد مجمّع الاتصالات في e2e (`QueuePool limit of size 5 overflow 10 reached`، ولوجين يفشل بعد 30s) | مسار SSE كان يحجز اتصالًا لكل تبويب لطول عمر البثّ (جلسة الاعتماد تُغلق مع نهاية الاستجابة) | `authenticate()` بجلسة قصيرة تُغلق قبل أول إطار + ميزانية مجمّع SQLite صريحة (10+20) لا افتراضية (5+10) | `test_an_open_stream_does_not_pin_a_database_connection` (يفشل على الكود القديم بـ`TimeoutError`، وينجح الآن) |
| 14 | `runbook.md` | لا قسم «قبل الوضع الحيّ» | قسم ب.١: جدول الحواجز **منقول حرفيًا** من `switch_blockers()`/`FIELD_LABELS` + أمر `preflight` + نصّ «لم يُجرَّب اتصال بمزوّد حقيقي» | `docs/runbook.md` |

**بوابات R7 (مخرجات حرفية):** `ruff check .` ⇒ `All checks passed!` · `ruff format --check .` ⇒
`165 files already formatted` · `mypy app` ⇒ `Success: no issues found in 113 source files` ·
`pytest -q` ⇒ **280** مجموعة · **0** فشل · exit 0 (10 مُتخطَّاة `postgres_only`) · `smoke_api.py` ⇒
`SMOKE OK — 126/129 documented calls answered` · `live_matrix.py` ⇒
`checks: 231 · passed: 231 · P0 failed: 0 · warnings: 0` · `check_openapi_fresh.py` ⇒
`openapi.json is fresh (110 paths)` · الواجهة: `eslint` نظيف · `tsc` (app + e2e) نظيف ·
`vitest` ⇒ **121/121 في 17 ملفًا** · `build` ⇒ `✓ built in 7.76s` · `codegen:api` ⇒ 146 مخططًا ·
**e2e في CI فقط** (لا متصفح هنا): التشغيل `40621482249` أخضر بالكامل —
`192 اختباراً · 144 ناجح · 0 فاشل · 48 مُتخطٍّ · flaky=0` (`desktop` 92/4/0 · `mobile` 52/44/0)، واختبارا
R7 (`r7-round.spec.ts`) نجحا على `desktop`. طريقان للوصول إليه: التشغيل `40610776389` سقط بسبب خلل في
الاختبار (بريد بوابة من بذرة pytest لا من بذرة e2e)، والتشغيلان `40614610028`/`40618435086` كشفا **عيبًا
حقيقيًا**: نفاد مجمّع الاتصالات وراء كل تسجيل دخول يفشل (البند 13). أُصلح الاثنان، والتفصيل في §6 من التقرير.

**ما لم يُدَّع:** لا اتصال فعلي بـMeta ولا بسنترال حقيقي (لا حساب ولا رقم مرخّص في هذه البيئة)؛ المثبت
عقد موقَّع ومختبَر (توقيع/تكرار/تسريب/SSRF) + خطوات المشغّل السبعة في الـrunbook. راجع
[التقرير](qa/r7-production-hardening-2026-10-28.md) §3.

## جولة R8 — «الحمل والتحمّل»: أرقام حقيقية على PostgreSQL، وعيب واحد P0 أُصلح

التقرير الكامل: [qa/load/r8-load-2026-10-28.md](qa/load/r8-load-2026-10-28.md) (كل رقم فيه مقتبس من
`docs/qa/load/raw/`). البيئة: شجرة `073891b` + تغييرات الجولة · uvicorn **عمّال واحد** على 2 vCPU/3 GB ·
PostgreSQL 16.2 · `pool_size=10 + overflow 20` · `INTEGRATIONS_DRIVER=sim`.

| # | السيناريو | النتيجة المقيسة | الحكم |
|---|---|---|---|
| S1 | 10 مستخدمين × 2 د | 2,165 طلبًا · 0 خطأ · 18.16 ط/ث · p95 96 | ✅ |
| S2 | سلّم 10→100 (دقيقتان/درجة) | صبيب 17.7 / 36.3 / 34.5 / 35.7 / 35.1 · أخطاء **صفر** · p95 130 / 460 / 1700 / 4100 / 7100 | السقف عند ~41 ط/ث |
| S4 | 40 بثًّا × 5 دقائق + 241 تسجيل دخول | 40/40 بثًّا 200 · أول إطار p95 374ms · logins p95 **351ms** · اتصالات PG 11/11/11 | ✅ |
| S5 | كتابة 25 مستخدمًا × 3 د | 5,993 طلبًا · **0 فشل** · صفر 409 وصفر 500 | ✅ |
| S6 | تحمّل 25 دقيقة قبل الإصلاح ثم 20 بعده | 62,350 ثم 49,105 طلبًا · خطأ 0.05% و0.03% (كلها تجديد رمز) · p95 **تحسّن** 542→315 و558→351 · بلا تسرّب (RSS 179MB ثابت) | ✅ انزياح 0% |
| S3 | 25 سياق متصفح × 10 د | **شُغِّل في CI** (`40696375008`): 3,859 دورة · **3,671 ناجحة = 95.1%** · 8,521 خطوة · فشل 305 · p50 618 / p95 3,574ms · **صفر `pageerror` وصفر 5xx** | ✅ (محليًا لا متصفح؛ المحاولتان 90.8% و85.4% محفوظتان مع سبب كل فشل) |

**العيب P0 الذي وُجد بالإصلاح:** `POST /auth/login` كان يتحقّق من كلمة المرور (Argon2 ≈110ms) **قبل**
إغلاق المعاملة، فيحجز اتصالًا من المجمّع وهو لا يفعل شيئًا سوى الانتظار؛ تحت حمل متزامن صار
`QueuePool limit of size 10 overflow 20 reached` و**طلبان بـ500** بعد 19–35 ثانية. الإصلاح: `await
session.commit()` قبل الـhash + `tests/test_auth_pool.py` (يفشل على الكود القديم: انتظار 1.12s، وينجح
الآن) + إعادة قياس: **صفر استجابات غير 200** في السيناريو نفسه (`raw/api-server-after-queuepool-count.txt` = 0).

**الحدّ المعروف (بلا تجميل):** الصبيب مسقوف عند ~41 ط/ث لأن العمّال الواحد يستهلك ~19 مللي CPU لكل طلب
(78% من نواة عند 41 ط/ث)؛ رفع التزامن يضيف صفًّا لا صبيبًا (p95 7.1s عند 100 مستخدم). وزيادة العمّال
تتطلّب أولًا نقل البثّ الحيّ من `asyncio.Queue` داخل العملية إلى `LISTEN/NOTIFY`، ورمز تحديث لكل جلسة
(اليوم `refresh_token_version` واحد للمستخدم ⇒ جلسة ثانية تُبطل الأولى: 32 ثم 16 حالة في S6).
ولذلك: `SAFE_CAPACITY = {browser: 25, api_vu: 25} — measured` (وما فوق 25 يستحق التوسّع الأفقي أولًا).

## جولة R10 — «ERP أساسي»: الدفتر، والمستحقات، والعمولات، والصندوق، ولوحة الإدارة

التقرير الكامل بالأوامر ومخارجها: [qa/r10-basic-erp-2026-10-28.md](qa/r10-basic-erp-2026-10-28.md).

| # | الوعد | الإثبات الحرفي |
|---|---|---|
| 1 | دفع القسط يكتب قيدًا واحدًا في الدفتر **في نفس المعاملة** | `test_paying_an_installment_creates_ledger_entry` · smoke: «the cashbox balance moved by the amount» |
| 2 | الإلغاء يعكس الرصيد ولا يحذف الصف (سبب + مستخدم + وقت + تدقيق) | `test_void_ledger_entry_reverses_cashbox_balance` · live_matrix: «the void reverses the balance exactly» |
| 3 | لا دفع عمولة مرتين (`commission_id` فريد + 409) | `test_commission_cannot_be_paid_twice` · live_matrix: «paying twice → 409» |
| 4 | الأعمار من تاريخ الاستحقاق، و«غير المستحق» ليس متأخرًا | `test_ar_aging_buckets` (10/45/75/120 يومًا) · live_matrix: «five aging buckets, not four-and-a-guess» |
| 5 | كل رقم في لوحة الإدارة يطابق استعلامه | `test_executive_dashboard_numbers_match_source_queries` + 4 فحوص تطابق بين المسارات في live_matrix |
| 6 | مندوب بلا `finance.view` ⇒ 403 على كل مسار مالي | `test_rep_without_finance_view_gets_403` · live_matrix (مندوب/CS/بوابة) |
| 7 | مبلغ غير موجب ⇒ 422، واتجاه مخالف للنوع ⇒ 422 | `test_ledger_rejects_non_positive_amount` · live_matrix |
| 8 | الشاشات تعرض أرقام الـAPI ولا تخترع هدفًا | `frontend/tests/financeRound.test.tsx` |

البوابات على هذا الفرع: `ruff` نظيف · `mypy` (120 ملفًا) نظيف · `pytest` **293 passed, 9 skipped** ·
`smoke_api` **137/140** · `live_matrix` **274/274 بلا P0** · `openapi` طازج (120 مسارًا) · الواجهة
`lint`+`typecheck`+`build` ✓ · `vitest` **126/126 في 18 ملفًا**.
**ما لم يُشغَّل:** Playwright/لقطات الشاشة (لا متصفح في هذه البيئة — تُقاس في CI)، و`check_core_coverage`
(أداة `coverage` غير مثبَّتة)، و`postgres_only` (9) تُقاس في CI على PostgreSQL 16.

## جولة R10.1 — تصحيح على النقاط الثمانية (فوق R10 المدموجة)

التقرير الكامل: [qa/r10-basic-erp-2026-10-28.md §9](qa/r10-basic-erp-2026-10-28.md). لا وحدات جديدة،
ولا توسيع نطاق — كل بند إما أُصلح في الكود أو قيس بمخرج أمر.

| # | نقطة المراجعة | ما تم |
|---|---|---|
| 1 | الميرج بلا إذن | التسليم **PR مفتوح** يتوقف عند كلمتك؛ لا ميرج في هذه الجولة |
| 2 | تذبذب `r6-round` (≤2 ثانية) | أُعيدت قراءة دليل التشغيل: الإطار وصل والتاب أعاد الاستعلام فردّ بصفوف **قبل** التغيير ثم صمت 17 ثانية. استُبعدت R10 بالدليل (لا topic مالي، ولا ذاكرة) و«النشر قبل الـcommit» باختبار جديد 40/40 (`backend/tests/test_realtime_race.py`). الاختبار **موقوف بـ`test.fixme`** ومسجَّل §9.2 — الآلية الباقية غير مثبتة ولم أخترع سببًا |
| 3 | «137/140» | الثلاثة **إعفاءات موثَّقة** (`media/{key}`، `portal/attachments`، `realtime/stream`)، ونفس الثلاثة حرفيًا قبل R10 (**126/129**) ⇒ ليست فشلاً ولا من R10 |
| 4 | الإلغاء مقابل القسط | **إصلاح حقيقي:** الإلغاء يعيد الواقعة — القسط يصبح غير مدفوع ويُحصَّل بإيصال جديد، والتسوية تعود `approved`؛ + `reopened` في الاستجابة + `409` يسمّي الإيصال وطريق الرجوع. اختباران جديدان |
| 5 | `issued_at` | كان `0009` يملأ بـ**`created_at`** (تاريخ الحجز). ترحيل جديد `0010_policy_issue_date` يأخذ التاريخ من `audit_logs.policy.issued` ويترك المجهول `NULL`، والحساب بلا `coalesce` + تنبيه `issue_date_unknown` |
| 6 | الصندوق | الشاشة تكتب صراحةً: صافي حركة النقدية من `since`، **لا رصيد افتتاحي ولا تقفيل يومي**؛ والـAPI يعيد `since` |
| 7 | توقيت استحقاق العمولة | **لم يُقرَّر بصمت:** ثلاثة خيارات معروضة في §9.7 من تقرير الجولة — ينتظر قرارك، والوضع الحالي (الاستحقاق عند الإصدار) باقٍ حتى تقول غير ذلك |
| 8 | بوابة التغطية | موجودة في CI (`.github/workflows/ci.yml` → `scripts/check_core_coverage.py`) ولم تكن تشمل وحدات R10؛ أُضيفت: `finance 86.1%` · `executive 98.9%` · `installments 96.7%` (الحد 80%) |

البوابات بعد التصحيح: `pytest` **293 passed, 9 skipped** · `smoke_api` **137/140** (نفس الإعفاءات) ·
`live_matrix` **274/274، 0 P0** · `openapi` طازج · `vitest` **128/128 في 18 ملفًا** · `lint`/`typecheck`/`build` ✓ ·
`check_core_coverage` **17 وحدة كلها ok**.

## قرارات اتُّخذت نيابة عن المالك (لأن الأسئلة غير مسموحة)

| قرار | ملف |
|---|---|
| 001 الهيكل والمكدّس والـstack الثابت | [`decisions/001-architecture.md`](decisions/001-architecture.md) |
| 002 أرضيات الاعتماديات و`pip-audit` | [`decisions/002-dependency-floors.md`](decisions/002-dependency-floors.md) |
| 003 SQLite للوحدة وPostgres للتكامل (+وضع RLS الحالي) | [`decisions/003-sqlite-tests-postgres-integration.md`](decisions/003-sqlite-tests-postgres-integration.md) |
| 004 interpol واحد في react-i18next | [`decisions/004-i18n-interpolation.md`](decisions/004-i18n-interpolation.md) |
| 005 خصوصية البوابة بالحذف من النموذج | [`decisions/005-portal-privacy-by-absence.md`](decisions/005-portal-privacy-by-absence.md) |
| 006 Playwright واللقطات في CI | [`decisions/006-e2e-screenshots-in-ci.md`](decisions/006-e2e-screenshots-in-ci.md) |
| 007 تخزين الوسائط الواردة | [`decisions/007-inbound-media-staging.md`](decisions/007-inbound-media-staging.md) |
| 008 المرحلة الثانية خلف `feature_flag` | [`decisions/008-phase2-feature-flags.md`](decisions/008-phase2-feature-flags.md) |
| 009 سجل شركات التأمين (37 لا 47) | [`decisions/009-insurer-register.md`](decisions/009-insurer-register.md) |
| 012 جدول تدقيق الاعتماديات | [`decisions/012-dependency-audit.md`](decisions/012-dependency-audit.md) |
| 010 التصدير: XLSX الآن وPDF مؤجَّل | [`decisions/010-exports.md`](decisions/010-exports.md) |
| 011 `/metrics` وmailpit والمحاكيات داخل العملية | [`decisions/011-monitoring-and-mailpit.md`](decisions/011-monitoring-and-mailpit.md) |
| 013 سطح الـAI: محاكاة حتمية ومراجعة قبل أي كتابة | [`decisions/013-ai-surface-sim-only-and-review-before-write.md`](decisions/013-ai-surface-sim-only-and-review-before-write.md) |
| 014 سلسلة الترقية على PostgreSQL: ما انكشف، استثناء تحرير 0001/0002، وتوصيل RLS | [`decisions/014-migration-chain-run-for-real-on-postgres.md`](decisions/014-migration-chain-run-for-real-on-postgres.md) |
| 015 تصحيح مقدّمات e2e، والهبوط بالصلاحية، والشارة بالعدد الحقيقي | [`decisions/015-e2e-premises-corrected-and-permission-landing.md`](decisions/015-e2e-premises-corrected-and-permission-landing.md) |
| 016 إعدادات التكاملات في القاعدة: تخزين، أسرار مشفّرة، شارة حيّ/محاكاة، وسجلّ webhook | [`decisions/016-integrations-settings.md`](decisions/016-integrations-settings.md) |
| 017 مقاعد المكتب، استيراد Leads من ملف، وإعادة توزيع حقيقية: ثلاثة أسئلة «مَن يستطيع» و«أين تُحفظ الصفوف» | [`decisions/017-team-seats-lead-import-and-redistribution.md`](decisions/017-team-seats-lead-import-and-redistribution.md) |
| 018 قشرة الجوال | [`decisions/018-mobile-shell.md`](decisions/018-mobile-shell.md) |
| 019 نطاق مشروع الجوال في e2e (الحوارات على سطح المكتب) | [`decisions/019-e2e-dialog-scope.md`](decisions/019-e2e-dialog-scope.md) |
| 020 الوسائط بلا MinIO | [`decisions/020-object-storage-without-minio.md`](decisions/020-object-storage-without-minio.md) |
| 021 إصدار الوثيقة: الملف هو التسليم | [`decisions/021-policy-issuance-documents.md`](decisions/021-policy-issuance-documents.md) |
| 022 حساب بوابة العميل: من ملف العميل، وباسم مستخدم | [`decisions/022-portal-accounts-and-username-login.md`](decisions/022-portal-accounts-and-username-login.md) |
| 023 القنوات الحيّة تُثبَت بعقد: توقيع، تسجيل يُنزَّل، وفحص وصول حقيقي | [`decisions/023-live-channel-contracts.md`](decisions/023-live-channel-contracts.md) |
| 024 سعة الأعمدة النصية: PostgreSQL يرفض ما يتجاهله SQLite — 32 لـ`webhook_events.status` + قصّ دفاعي | [`decisions/024-text-column-width-postgres.md`](decisions/024-text-column-width-postgres.md) |
| 025 مسارات التنزيل: البادئة `/api/v1` مرة واحدة، والرابط المطلق يُترك كما هو | [`decisions/025-download-url-shape.md`](decisions/025-download-url-shape.md) |

## قائمة ما قبل «جاهز للمراجعة»

- [x] parity للتوكنات مقصوصة من §4 ومحمية باختبار (`frontend/tests/tokens.test.ts`: 20 تأكيدًا على القيم نفسها)
- [x] عزل المندوب مُختبَر بالـ404 (backend matrix + `e2e/isolation.spec.ts`)
- [~] لم تُعدَّل migration مُدمجة **إلا باستثناء موسَّم ومُبرَّر** (0001/0002، ADR-014): كل منهما كان
  مستحيل التشغيل على PostgreSQL فلم تُطبَّق على أي قاعدة. أي تغيير آخر يُضاف كترقية جديدة (0003 فما فوق)
- [x] لا أسرار في المستودع: `.env` مُستثنى، والموجود أمثلة فقط (`backend/.env.example`, `frontend/.env.example`, `infra/.env.example`)
- [x] لا بيانات شخصية حقيقية في الـseed (أسماء/أرقام وهمية، نطاق `@brokerhub.test`، `+201…` غير مخصّصة)
- [~] لقطات الـ16 شاشة + contact sheet: مكتوبة وتُنتَج في CI؛ لم تُشغَّل محليًا — جُرِّب `@sparticuz/chromium`
  وتعذّر التشغيل لغياب `libnss3/libnspr4` بلا root (تفصيل «ما لم يُشغَّل» 1)
- [x] README فيه تشغيل بأوامر ثلاثة + تشغيل بلا Docker
- [x] runbook + STATUS + ui-parity (README + screens.md بكل فوارق §9) + سجل القرارات 001–017 بلا فجوة
- [x] **كل نقطة موثّقة تجيب**: `python scripts/smoke_api.py` يعبر على كل `(path, method)` في الوثيقة
  (114/116 على 98 مساراً، والاستثناءان بسببهما مكتوب) — خطوة CI قائمة بذاتها بعد بوابات التغطية
- [x] CI: ruff+mypy+pytest (على PostgreSQL أيضًا)+بوابة تغطية النواة+تدقيق الاعتماديات+فحص staleness
  للـOpenAPI+**منع تحرير الترقيات**+**سلسلة الترقية على قاعدة PostgreSQL فارغة**؛ lint/typecheck/vitest/build
  للواجهة؛ e2e مع اللقطات؛ ووظيفة `docker` صارت **تُقلع الـstack بالكامل** (health + nginx + seed + login)
