# مطابقة الشاشات الست عشرة (المرجع: ملف «تصميم الواجهات»)

لكل شاشة: ما تقوله الورقة، ما ينفّذه الكود (ملف/سطر قابل للفتح)، والفارق. الأرقام التي في الورقة
من بيانات تجريبية ليست متاحة للتطبيق، لذلك لا تُطبع أبداً في الكود؛ تُحسب من الـ API.
**لا تُكتب كلمة «مطابقة» قبل توقيع صاحب المنتج.**

القياس: متغير `viewport` لكل مشروع (سطح المكتب 1440×900 والجوال 390×844). لقطة الشاشة تُحفظ في
`docs/screenshots/NN-<slug>.png` بالأسماء الثابتة التي تطلبها §10 (`01-dashboard.png` … `16-login.png`،
و`04-mobile-leads.png` وحدها بمقاس الجوال)، ثم يُعاد قياس العنصر من نفس المتصفح داخل `page.evaluate()`
بـ `getBoundingClientRect()` (CSS pixels، بدون `deviceScaleFactor`) ومقارنته بقيمة الورقة.
مصدر الحقائق: النصوص العربية في `frontend/src/lib/i18n/ar.ts` — لا نصّ عربي مصفوف في المكوّنات،
لذا ما تُطابقه الاختبارات هو ما يراه المستخدم فعلاً.

---

## 1. لوحة التحكم — `frontend/src/features/dashboard/DashboardPage.tsx`

| ورقة التصميم | الكود | حالة |
|---|---|---|
| بطاقات: طلبات اليوم، متوسط الاستجابة، SLA، تحويلات | بطاقات من `GET /dashboard/summary` فقط | ✅ |
| `148` / `94%` | `formatNumber(summary.requests_today)`، `formatPercent(sla_rate)` | ✅ (القيمة من الـ API) |
| `+12%` و«أسرع بـ4 دقائق» | يُشتقّان من `requests_yesterday` و`avg_first_response_delta_minutes` | ⚠ فارق 1 |
| «الهدف 90%» و«تحتاج تدخلاً» | نصّ ثابت لا يُحسب من شيء | ⚠ فارق 2 |
| «الثلاثاء 3 نوفمبر · آخر تحديث قبل دقيقتين» | سطر التاريخ `Intl` + `refetchInterval` | ✅ |
| بحث في الهيدر | نموذج بحث → `/inbox?q=` | ✅ |

1. النسبة المئوية للتغيّر تُحسب من الأمس؛ إن لم توجد بيانات أمس تُعرض «—» بدلاً من «+12%».
2. «الهدف 90%» قيمة سياسة لم تُحدَّد بعد: الهدف ليس في الـ API، لذلك يُعرض كعنوان لا كالتزام.
3. الهيكل متجاوب منذ R3: الشريط الجانبي 264px يُعرض عند `lg` فأعلى فقط، وتحته شريط تنقّل سفلي ثابت
   باسم وصول مختلف («التنقل السريع») — ADR-018. قبل ذلك كان الشريط يأخذ 264 من 390 بكسل، فانكمش
   عنوان KPI إلى عرض صفر وفشل قياس الجوال في CI (التشخيص كامل في ADR-018).

## 2. صندوق الطلبات (3 أعمدة) — `features/inbox/InboxPage.tsx`

| ورقة | كود | حالة |
|---|---|---|
| عمود قائمة 330px | `w-[330px] shrink-0 border-x border-line` (سطر 171) | ✅ |
| تفاصيل 270px | `w-[270px] shrink-0 border-r border-line` (سطر 275) | ✅ |
| مشغّل صوتي 20 شريطاً بارتفاعات مختلفة | `BARS = 20`، `HEIGHTS` مشتقة من `seed` لكل رسالة، 2px → 16px | ✅ |
| «العميل لم يحدد اسم الشركة» | `inbox.callerUnknown` | ✅ |
| لا نسخة موبايل من هذه الشاشة (3 أعمدة كما في الورقة) | `w-[330px] shrink-0` + `w-[270px] shrink-0` بلا تجميع عند `lg` | ✅ كما في التصميم — والاختبارات التي تكتب داخل الخيط `desktop`-only بسبب ذلك (السبب مكتوب في رأس `frontend/e2e/inbox.spec.ts`)؛ سطح الموبايل في هذا المنتج هو شاشة 6 |
| ردّ داخلي/خارجي، مرفقات، مكالمة | تبويبات + رفع مرفق + `POST /dev/sim/call` | ✅ |

الفارق: زر «اتصال» يفتح نافذة تسجيل مكالمة (مدة + ملاحظة) لأنه لا يوجد WebRTC في المرحلة الأولى؛
التسجيل الصوتي نفسه يأتي من المحاكي/المزوّد لا من المتصفح.

## 3. كانبان المندوب — `features/leads/KanbanPage.tsx`

| ورقة | كود | حالة |
|---|---|---|
| 5 أعمدة × بطاقات، شريط عزل للمندوب | `KANBAN_COLUMNS` + لافتة `leads.isolationBanner` (19 عميلاً لا 63) | ✅ |
| «إتمام التحويل» بلون `#05603A` | `style={{background:"#05603A"}}` في تفاصيلLead وزر التحويل | ✅ |
| تأكيد قبل التحويل وفقدان | نافذتا `ConfirmDialog` مع السبب والإيميل | ✅ |
| سحب وإفلات | `dnd-kit` مع نقل تفاؤلي ثم تسوية من الخادم | ✅ |

## 4. الجوال — `features/leads/MobileLeadsPage.tsx`

| ورقة | كود | حالة |
|---|---|---|
| عرض 390×844، شريط سفلي 84px | `grid h-[84px] grid-cols-4 items-center` | ✅ |
| زر عائم 56px (`FAB`) | `fixed bottom-[100px] grid h-14 w-14` | ✅ |
| قائمة بطاقات بنطاق المندوب | نفس `/leads?scope=mine` المستخدم في التابلت | ✅ |

## 5. ملف العميل — `features/clients/ClientFilePage.tsx`

| ورقة | كود | حالة |
|---|---|---|
| أفاتار 64px + اسم 22px | `<Avatar size={64}>` + `text-[22px]` | ✅ |
| 4 مؤشرات | ما يعرضه الكود فعلياً: `clients.file.kpiStaff` (الموظفون المؤمَّن عليهم) · `kpiOpen` (طلبات مفتوحة) · `kpiResponse` (متوسط زمن الرد) · `kpiRenewal` (التجديد بعد، بنبرة `r` عند ≤ 60 يوماً). ورقة التصميم ليست داخل الريبو، فالمُثبَّت هنا هو سلوك الكود؛ وأي اختلاف في الأسماء عن الورقة فارق نصي يُصحَّح عند المراجعة البصرية | 🟡 |
| تبويبات segmented (وثائق/مكالمات/فواتير/…) | أزرار `rounded-pill` + `aria-selected` | ✅ |
| «فتح بروفايل صاحب الشركة» | **لا يوجد** | ⚠ فارق 3 |

3. لا شاشة إدارة لمستخدمي البوابة في الكود؛ الزر الذي يفتحها غير منفَّذ، ولا مسار `POST
   /clients/{id}/portal-users` معروض في الواجهة (الخادم يدعمه).

## 6. بوابة العميل — `features/portal/PortalPage.tsx`

| ورقة | كود | حالة |
|---|---|---|
| عرض أقصى 1100px بدون سايدبار | `mx-auto max-w-[1100px]` + `PortalShell` | ✅ |
| بانر داكن بلون `--sidebar` | `style={{background:"var(--sidebar)"}}` | ✅ |
| عدد الموظفين المغطى 44px | `text-[44px] font-bold tabular-nums` | ✅ |
| 3 مؤشرات أعلى اليمين | `Kpi` × 3 | ✅ |
| كرت وثائق بعرض 330px | `aside class="w-[330px] shrink-0"` | ✅ |
| «ممتاز» تحت مؤشر الرد | مشتقة من `responseRating(avg_response_minutes)` (≤30 ممتاز، ≤90 جيد، أقل بطيء) | ⚠ فارق 4 |

4. الورقة تعرض «ممتاز» كقيمة ثابتة؛ التطبيق يصنّف الرقم الفعلي. عند غياب بيانات الردود تظهر «—»
   بدلاً من ادعاء تقييم.

## 7. شاشة المدير (توزيع المهام) — `features/leads/SalesTeamPage.tsx`

| ورقة | كود | حالة |
|---|---|---|
| كروت مندوبين + «حِمل مرتفع» | `leads.loadHigh` عند `active > 20` | ✅ |
| أشرطة مراحل 10px | `h-[10px] rounded-pill` | ✅ |
| حوض «غير مسند» + إعادة توزيع بطلب واحد | `GET /leads?unassigned=true` + `POST /leads/assign-bulk` (`{unassigned_only, to:[…]}`، لا حلقة `PATCH` على صفحة واحدة) | ✅ |
| استيراد Excel/CSV من اللوحة | زر `leads.importLeads` عند `leads.view_all` فقط → `frontend/src/features/leads/ImportLeadsDialog.tsx` (معاينة ⇒ `POST /leads/import/{job}/commit`) | ✅ |
| أعضاء المكتب (مقاعد + أدوار + كلمة مرور مؤقّتة) | `frontend/src/features/leads/TeamMembersCard.tsx` فوق `GET/POST/PATCH /sales-team/members[/{user_id}]`، بسقف `OFFICE_ROLES` وصلاحية `leads.assign` | ✅ |
| لوحة الحِمل تُظهر الموقوف ولا يمنحه منتقي التوزيع | `RepWorkloadOut.is_active` ⇒ `rows.filter((row) => row.is_active)` في `RedistributeDialog` | ✅ |
| قاعدة «من يستطيع استلام Lead» في مكان واحد | `TeamMemberOut.accepts_leads` (من `leads.view_own`) + `leads._require_rep` يرفض مقعد خدمة العملاء بـ 422 — قبل R3 كان يُقبل فيختفي الـ lead من كل لوحة | ✅ (R3) |
| قائمة المستلمين ثابتة الترتيب | `repositories/leads.rep_workload` بـ `order_by(User.full_name)` — بلا ترتيب كان «أول بياع» هو ما يعيده المخطِّط | ✅ (R3) |

لا يوجد في هذا المنتج «جدول كل الـ Leads» كشاشة مستقلة؛ إسناد الصف colocated مع الحوض ومن تفاصيل الـ lead
(ADR-017 §7). الأرقام الحيّة لكل سطر أعلاه في `docs/STATUS.md` («جولة R2» و«جولة R3»).

## 8. قائمة العملاء — `features/clients/ClientsPage.tsx`

تحويلة خضراء أعلى القائمة، وصف `--row-ok` للصفوف التي تحتاج متابعة، بحث + فرز + ترقيم. ✅ بلا فوارق.

## 9. تفاصيل الـ Lead — `features/leads/LeadDetailPage.tsx`

| ورقة | كود | حالة |
|---|---|---|
| شريط المراحل: دائرة 34px وهالة 5px | `Stepper.tsx`: دائرة 34px و`boxShadow: 0 0 0 5px var(--primary-soft)` | ✅ |
| زر تحويل أخضر `#05603A` | `style={{background:"#05603A"}}` | ✅ |
| تسجيل نشاط/ملاحظة/مكالمة | تبويبات على `/leads/{id}/activities` | ✅ |

## 10. lead جديد + فحص التكرار — `features/leads/NewLeadPage.tsx`

`duplicateInfo()` يطابق الموبايل/البريد/اسم الشركة ويعرض عدد النتائج في رسالة واحدة، والإرسال
يُحظر حتى يُصرَّح `allow_duplicate` (نفس العقد مع الخادم). ✅

## 11. استيراد الموظفين من Excel — `features/clients/InsuredPage.tsx`

تجفيف ← تأكيد ← تقرير: تُعرض `total_rows`/`will_create`/`will_update`/`rejected` بعد
`GET /import-jobs/{id}` ثم `POST /import-jobs/{id}/commit`. ✅ بلا فوارق بعد إصلاح مفتاح
`stats` وأزرار التأكيد المعطّلة عند تعذّر الاتصال.

## 12–14. بوليصات / إصدار / تقارير — `features/phase2/PoliciesPage.tsx` · `features/phase2/IssuingPage.tsx` · `features/phase2/ReportsPage.tsx`
> API: قائمة الإصدار تُضمّن قائمة المستندات لكل حالة؛ وتوفّر أيضاً `GET /issuing-cases/{case_id}` و
> `GET /issuing-cases/{case_id}/documents` لنفس البيانات بنطاق مُطبَّق (`with_scope(kind="issuing_case")`).
> الشاشة الحالية تستخدم القائمة فقط — لا فرق بصري، والزر «تحديث» يعيد جلبها.


| ورقة | كود | حالة |
|---|---|---|
| مؤشرات بوليصات من قاعدة البيانات | `GET /policies/kpis` (`PoliciesKpiIn` في العقد) | ✅ |
| ألوان أداء الشركات ≥90 أخضر / 80–89 كهرماني / <80 أحمر | `#10B981` / `#F59E0B` / `#F04438` حرفياً | ✅ |
| تصدير Excel | `POST /api/v1/exports/insurer-performance` ← نفس `services/reports.py` الذي تقرأه الشاشة، سقف 5000 صّف، `X-Export-Truncated`، وصف `audit_logs` | ✅ |
| تصدير PDF | زر معطّل بالسبب `phase2.exportPdfPending` | ⚠ فارق 5 |
| هذه الشاشات «غير مفعّلة بعد» | بوابة `feature_flag` في `features/phase2/phase2Gate.tsx` | ✅ |

5. XLSX مُنفَّذ ومختبَر؛ PDF مؤجَّل بقرار موثّق في `decisions/010-exports.md` (تشكيل العربية + خط مضمّن)، والزر يعرض السبب بدل أن يكون زراً كاذباً.

## 15. المستخدمون والصلاحيات — `features/users/UsersPage.tsx`

مفتاحان 40×22 لكل صف (`components/ui.tsx`: `h-[22px] w-[40px]`)، مصفوفة أدوار/صلاحيات من
`GET /permissions` (18 صلاحية × 7 أدوار)، وتعطيل مستخدم لا حذف. ✅

ملاحظة (ADR-016): الشاشة صارت تبويباً داخل `/settings` («المستخدمون والصلاحيات»)، و`/users` إعادة
توجيه إلى `/settings?tab=users`. التبويب لا يظهر إلا لمن يملك `users.manage`، ولذلك تُلتقط اللقطة
بدور `admin`: دور `sales_manager` يملك `settings.manage` ولا يملك `users.manage` — لقطة قديمة باسم
`manager` كانت تصوّر حالة الـ403 لا الشاشة.

### 15ب. مركز الإعدادات — `features/settings/SettingsHub.tsx` (خارج الورقة، لا مطالبة بمطابقة)

| العنصر | ما في الكود | حالة |
|---|---|---|
| ثلاث بطاقات مزوّدات | `PROVIDERS` في `features/settings/model.ts` + حقول مولّدة من مفاتيح `config` التي يرجعها الخادم | ✅ (لا قائمة منسوخة؛ بوابة انحراف في `frontend/tests/settingsView.test.tsx`) |
| الحقول السرية | `type="password"` بلا قيمة، و`placeholder` هو القناع `••••••••`؛ «حذف» يرسل `null` | ✅ مثبت باختبار رسم DOM |
| شارة حيّ/محاكاة/غير مُعَد/خطأ | خمس نغمات معتمدة فقط: `t`/`b`/رمادي/`r` (`STATUS_BADGE`)؛ لا سادس | ✅ |
| التحوّل إلى الحيّ | `Modal` تأكيد + قائمة معطِّلات من `liveBlockers` (اختبار ثم حقول)؛ الرجوع بلا تأكيد | ✅ |
| رابط الـwebhook + نسخ | `GET /settings/integrations/webhook-urls` و`navigator.clipboard` مع سبب عند الفشل | ✅ |
| مزامنة القوالب | تُرجع قوالب المحاكاة مع «المحاكاة» في النص؛ لا ادّعاء بأنها من Meta | ⚠ لا قيمة حيّة قبل ربط حساب Meta |
| آخر الأحداث الواردة | `GET /settings/integrations/events` (آخر 8 من 20 في 24 ساعة) | ✅ |
| سياسة الاكتتاب | `PUT /settings/policies`؛ المعاينة تعرض **عدد الأقساط الحقيقي** للخطة لا حساباً مختلقاً | ✅ (`INSTALLMENTS_BY_PLAN` بوابة انحراف مقابل `app/services/policies.py`) |

لا مقارنة بصرية لصاحب التصميم مع هذه الشاشة: لا ورقة لها في التسلسل (01…16)؛ اللقطة تُلتقط باسم
`15b-settings-hub.png` للمراجعة فقط (STATUS سطر 15).

## 16. تسجيل الدخول — `features/auth/LoginPage.tsx`

| ورقة | كود | حالة |
|---|---|---|
| لوحة يمين 380px + عنوان داكن 40px | `max-w-[380px]` + `text-[40px] font-bold` على لوحة `--sidebar` | ✅ |
| حقول 44px بخط 14 و`label` واضح | `.field` في `styles/index.css` | ✅ |
| «دخول العميل السريع» و«تواصل مع المبيعات» | `auth.quickPortal`, `auth.contactSales` | ✅ |

---

## شاشات R10 الخمس (ليست في ورقة التصميم الست عشرة — أُضيفت بطلب المالك)

كلها عربية RTL بنفس التوكنات، وكل رقم فيها من الـAPI (لا بيانات تجريبية في الواجهة). الترتيب في الشريط
الجانبي بعد «التقارير»، ومحميّة بالصلاحية نفسها التي يحميها الـAPI.

| الشاشة | المسار | الملف | الصلاحية | ما تقرأه |
|---|---|---|---|---|
| لوحة الإدارة التنفيذية | `/executive` | `frontend/src/features/executive/ExecutiveDashboardPage.tsx` | `reports.executive` | `GET /executive/dashboard` فقط |
| دفتر الحركات | `/finance/ledger` | `frontend/src/features/finance/LedgerPage.tsx` | `finance.view` (+`finance.post` للنموذج، +`finance.void` للإلغاء) | `GET/POST /finance/ledger`، `POST /finance/ledger/{id}/void` |
| مستحقات العملاء | `/finance/ar` | `frontend/src/features/finance/ArPage.tsx` | `finance.view` | `/finance/ar/summary` + `/finance/ar/clients` + `/finance/ar/clients/{id}` |
| العمولات | `/finance/commissions` | `frontend/src/features/finance/CommissionsPage.tsx` | `finance.view` (+`commissions.manage` للاعتماد/الصرف) | `/finance/commissions` + approve/pay |
| الصندوق | `/finance/cashbox` | `frontend/src/features/finance/CashboxPage.tsx` | `finance.view` | `GET /finance/cashbox` (الرصيد = صافي حركة نقدية من `since` — لا رصيد افتتاحي ولا تقفيل يومي) |

---

## قائمة الفوارق المجمّعة (تحتاج قرار صاحب المنتج)

| # | الشاشة | الفارق | النوع |
|---|---|---|---|
| 1 | لوحة التحكم | «+12%» و«أسرع بـ4 دقائق» تُشتقّان من الأمس، وتظهر «—» بلا بيانات | بيانات |
| 2 | لوحة التحكم | «الهدف 90%» ليس سياسة مسجّلة في الـ API | منتج |
| 3 | ملف العميل | لا يوجد زر/شاشة «فتح بروفايل صاحب الشركة» | نطاق |
| 4 | بوابة العميل | تصنيف الرد يُشتقّ من الرقم بدل «ممتاز» ثابت | بيانات |
| 5 | التقارير | PDF مؤجَّل بقرار (XLSX يعمل من الشاشة ومن قائمة الطلبات) | منصة |
| 6 | صندوق الطلبات | «اتصال» يسجّل مكالمة ولا يفتح WebRTC | منصة |
| 7 | عام | أرقام الورقة (148، 94%، 214 موظفاً، 37.5 دقيقة) ليست قيم افتراضية في الكود | بيانات |
