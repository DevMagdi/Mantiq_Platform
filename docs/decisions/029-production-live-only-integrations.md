# ADR-029 — Production runs live integrations only (WhatsApp + mobile phone calls)

- الحالة: مقبول (2026-10-28) · جولة R9
- المرجع: `backend/app/core/config.py`، `backend/app/integrations/{registry,sims,mobile_calls,whatsapp}.py`،
  `backend/app/api/v1/endpoints/{webhooks,dev_sim}.py`، `backend/tests/test_production_live_only.py`،
  `backend/scripts/live_matrix.py` (§4.9b)

## 1) المشكلة

قبل R9 كان السائق المحاكي هو **الافتراضي**، والـregistry يسقط إليه بصمت في حالتين:

1. `INTEGRATIONS_DRIVER=sim` (وهو الافتراضي في `.env.example` وcompose)،
2. مزوّد «حيّ» ناقص الإعداد — سطر `if state.is_live and token and phone_number_id: … else: WhatsAppSim()`.

النتيجة أن عملية إنتاج بلا توكن تعمل، وتعطي **200** على الـwebhook، وتعرض شاشة إعدادات خضراء، بينما كل
رسالة/مكالمة تُكتب في قائمة داخل الذاكرة. ولا توجد وسيلة لتمييز «حيّ فعلاً» من «محاكاة تُشبه الحيّ».
كما كان `provider: sim` خيارًا **مخزّنًا في القاعدة** داخل `integration_configs`، أي أن قرار «حاكِ مكالمات
العملاء» كان قرار بيانات لا قرار عملية. وكانت مسارات `/api/v1/dev/sim*` جزءًا من تطبيق الإنتاج (تحرسها
شروط داخل كل معالج).

## 2) القرار

**وضع العملية هو الذي يحدّد المحاكاة، لا الصف المحفوظ.**

- `ENV=prod` (و`production` مرادف) يفرض `INTEGRATIONS_DRIVER=live`؛ وكتابة `sim` صريحة **تفشل الإقلاع**.
- `Settings.simulation_allowed` صحيحة فقط في `ENV=dev|test`.
- السائقان المحاكيان في `integrations/sims.py` وحدهما، وبناؤهما يرفع `SimulationDisabled` خارج dev/test:
  الحاجز في المُنشئ، لا في نقطة استدعاء قد تُنسى.
- `registry.resolved()` يُعيد `is_live=True` دائماً في الإنتاج، حتى لو كان الصف يقول `is_live=false` أو
  كانت الذاكرة المخبأة (`_primed`) من عملية dev قديمة — العملية تتغلّب على الصف.
- مزوّد حيّ ناقص الأسرار يرفع `IntegrationNotConfigured` (503 `integration_not_configured`) برسالة عربية
  تسمّي المفتاح الناقص، بدل السقوط على المحاكاة.
- `registry.assert_production_ready()` يُستدعى في `lifespan` ويفشل الإقلاع في `ENV=prod` إن نقص أي سرّ حيّ.
- أسرار التطوير المنشورة (`dev-app-secret`, `dev-verify-token`, `dev-telephony-secret`) تُفرَّغ في الإنتاج:
  `hmac.new(b"dev-app-secret", …)` يحسبه أي قارئ للمستودع، فهو أسوأ من الغياب.
- مسارات المحاكاة في `endpoints/dev_sim.py`، وتُركّب من `create_app()` فقط عند `simulation_allowed`؛ في
  الإنتاج المسار **غير موجود** (404) وليس معالجًا يعيد 403.

## 3) مكالمات الموبايل

المفهوم المنتَجي ليس «سنترال/تليفون أرضي»: هي **مكالمة على رقم موبايل تملكه المنصة**، تصل من مزوّد
مكالمات موبايل (VoIP) إلى webhook موقَّع.

- المزوّد الموثَّق: **Twilio Programmable Voice** (`CallSid` / `CallStatus` / `From` / `To` /
  `CallDuration`, و`RecordingUrl` + `RecordingSid` للتسجيل)، مع بدائل شائعة (`uuid`/`status` من Vonage،
  `callId`/`type`، والأسماء الصريحة).
- `parse_event` يطبّع `ringing|in-progress|answered` ⇒ `call.started` و
  `completed|busy|failed|no-answer|canceled` ⇒ `call.ended`، وحدث تسجيل بلا `CallStatus` ⇒
  `recording_ready` (وإلا لَفُتحت مكالمة ثانية لمكالمة قائمة).
- الواردة تُنشئ/تربط **طلبًا بقناة `call`** بمفتاح رقم الموبايل، ويُحفظ على صف المكالمة مزوّدها الحقيقي
  (`twilio`/`generic`) بدل `"sim"` الثابتة، مع from/to/duration/status/recording.
- التسجيل يُنزّل من مضيف المزوّد فقط (حصر مضيف ضد SSRF) ويُخزَّن عندنا.
- المسار الرسمي `/api/v1/webhooks/mobile-calls`؛ و`/webhooks/telephony` مرادف موثَّق (deprecated في OpenAPI)
  حتى لا ينقطع مزوّد ضُبط قبل التسمية.
- المفتاح المخزَّن بقي `telephony` (عقد صف موجود وكل الاختبارات تشير إليه)، لكن كل نص للمشغّل يقول
  «مكالمات الموبايل» / «رقم الموبايل» / «مزود مكالمات الموبايل». صف قديم بـ`provider: sim` يُقرأ كـ`twilio`.

## 4) شاشة الإعدادات

ثلاث حالات فقط: **`Live`** / **«جاهز»** (`ready`، بدل `sim`) / **«غير مُعد»**، و`error` عند فشل آخر اختبار.
بطاقتا القنوات المعنيّتان بهذه الجولة هما واتساب للأعمال ومكالمات الموبايل؛ بطاقة البريد (SMTP) تبقى لأنها
مزوّد إشعارات لا قناة عملاء، وهي خارج نطاق R9 ولم تُوسَّع.
`preflight.sim_allowed` يعيد `false` في الإنتاج، فلا تُرسم «إيقاف الوضع الحيّ» أصلًا، و`set_live(false)` في
الإنتاج يعيد 409 واضحًا بدل التبديل الصامت إلى محاكاة غير موجودة.

## 5) ما لم يُدَّع

لا يوجد في هذه البيئة حساب Meta ولا حساب مزوّد مكالمات موبايل ولا رقم مرخّص. المثبت أن مسار `live` كامل
ومختبَر بعقد (URL، حامل التوكن، شكل JSON، HMAC على البايتات الخام، حصر المضيف، idempotency) وأن الإنتاج
لا يستطيع بناء سائق محاكاة. ما يلزم من المشغّل قبل أول رسالة/مكالمة حقيقية: `WHATSAPP_ACCESS_TOKEN` +
`WHATSAPP_PHONE_NUMBER_ID` + `WHATSAPP_APP_SECRET` + `WHATSAPP_VERIFY_TOKEN`، و`TELEPHONY_WEBHOOK_SECRET` +
`TELEPHONY_WEBHOOK_URL` + رقم الموبايل.
