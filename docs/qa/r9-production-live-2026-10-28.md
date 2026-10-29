# R9 — Production-only live integrations (WhatsApp + mobile phone calls), بلا محاكاة

- التاريخ: 2026-10-28 · الفرع الذي قيس عليه: `arena/0fa05dfd-insurance-platform-new`
- النطاق المغلق: (1) التكاملات في الإنتاج = Live فقط، (2) المكالمات = **مكالمات موبايل** وليست «سنترال أرضي».
- المرجع: [ADR-029](../decisions/029-production-live-only-integrations.md) · [runbook §6.1](../runbook.md)

## 1) كيف مُنع sim في production

| الطبقة | الملف | السلوك |
|---|---|---|
| الإعدادات | `app/core/config.py` | `ENV=prod` ⇒ `INTEGRATIONS_DRIVER` يُفرض `live`؛ `sim` صريحة ⇒ `ValidationError` (فشل إقلاع). `simulation_allowed` صحيحة في `dev|test` فقط. أسرار التطوير المنشورة تُفرَّغ. |
| المُنشئ | `app/integrations/sims.py` | `WhatsAppSim()` و`MobileCallsSim()` يرفعان `SimulationDisabled` خارج dev/test — الحاجز في المُنشئ لا في نقطة الاستدعاء. |
| الاختيار | `app/integrations/registry.py` | مزوّد حيّ ناقص الأسرار ⇒ `IntegrationNotConfigured` (503) بالعربية. `resolved()` يعيد `is_live=True` في الإنتاج مهما قال الصف أو الذاكرة المخبأة. |
| الإقلاع | `app/main.py` + `registry.assert_production_ready()` | `ENV=prod` بلا أسرار ⇒ `RuntimeError` يسمّي الناقص، ولا تبدأ العملية. |
| الوجهة | `app/services/integrations_settings.py` | `set_live(false)` في الإنتاج ⇒ 409 «لا يوجد وضع محاكاة»؛ حالة المزوّد `ready` بدل `sim`. |

## 2) أين اختفت/عُزلت `/dev/sim`

- المسارات انتقلت من `app/api/v1/endpoints/webhooks.py` إلى `app/api/v1/endpoints/dev_sim.py`.
- `create_app()` يركّبها فقط عند `settings.simulation_allowed` — في الإنتاج المسار **غير موجود**.
- كل معالج يستدعي `require_simulation_allowed()` كحاجز ثانٍ مستقل.
- إثبات: `test_dev_sim_routes_hidden_in_production` (404 عبر ASGI) و`test_dev_sim_routes_exist_in_a_dev_process`.

## 3) مسار مكالمة موبايل: webhook → طلب برقم الموبايل

`POST /api/v1/webhooks/mobile-calls` (والمرادف `/webhooks/telephony`) →

1. توقيع `X-Call-Signature: sha256=<hmac>` على البايتات الخام، وإلا 403 + صف `rejected` في `webhook_events`.
2. `parse_event`: Twilio (`CallSid`, `CallStatus`, `From`, `To`, `CallDuration`, `RecordingUrl`) + بدائل
   (`uuid`/`status`, `callId`/`type`, والأسماء الصريحة) → `call.started` / `call.ended` / `recording_ready`.
3. `intake.ingest_call_event`:
   - `call.started` ⇒ يفتح أو يربط طلبًا بقناة `call` بمفتاح رقم الموبايل، ويحفظ
     `CallRecord{provider_call_id, provider=<twilio|generic>, direction, from/to, caller_name, started_at}`،
     ورسالة `IN` في خيط الطلب؛ تكرار نفس `CallSid` ⇒ `duplicate` بلا صف ثانٍ.
   - `call.ended` ⇒ `ended_at` + `duration_s`.
   - `recording_ready` ⇒ `fetch_recording` (مضيف المزوّد فقط) ⇒ تخزين `calls/<call_id>.wav` ⇒ ربط بالطلب.
- إثبات: `test_mobile_call_webhook_opens_request_by_mobile` (توقيع خاطئ 403، موقَّع 200 و`request_id`،
  `channel == call`، `contact_mobile == +201277700011`، `provider == twilio`، التكرار `duplicate`،
  والمسار القديم ما زال 200).

## 4) نتائج الاختبارات (حرفيًا)

```text
$ pytest -q
284 passed, 9 skipped in 279.76s
$ pytest tests/test_production_live_only.py -q
12 passed
$ ruff check .        → All checks passed!
$ ruff format --check . → 176 files already formatted
$ mypy app            → Success: no issues found in 115 source files
$ python scripts/smoke_api.py → SMOKE OK — 126/129 documented calls answered
$ PYTHONPATH=. python scripts/live_matrix.py
checks: 238 · passed: 238 · P0 failed: 0 · warnings: 0
```

ضمن `live_matrix` مجموعة `production` (تحويل البيئة ثم قياس الكائنات الحقيقية ثم الاسترجاع):

```text
PASS production ENV=prod forces INTEGRATIONS_DRIVER=live            live
PASS production ENV=prod allows no simulation                       False
PASS production no credentials ⇒ no simulated driver (named failure instead) ['whatsapp', 'mobile-calls']
PASS production the boot gate fails closed on missing credentials   ENV=prod requires live integration credentials; …
PASS production both simulators are unconstructible in production   2/2
PASS production GET /dev/sim is 404 in a production build           404
PASS production POST /dev/sim/call is 404 in a production build     404
```

الاختبارات الأربعة المطلوبة في §4 من الجولة:

| الاسم | الملف | الحالة |
|---|---|---|
| `test_production_does_not_boot_sim_drivers` | `tests/test_production_live_only.py` | ✅ |
| `test_mobile_call_webhook_opens_request_by_mobile` | `tests/test_production_live_only.py` | ✅ |
| `test_whatsapp_live_rejects_empty_secret` | `tests/test_production_live_only.py` | ✅ |
| `test_dev_sim_routes_hidden_in_production` | `tests/test_production_live_only.py` | ✅ |

الواجهة: `npm run lint && npm run typecheck && npm run build` نظيفة، و`npx vitest run` = **121 passed** (17 ملفًا).
e2e لم تُشغَّل في هذه البيئة (لا متصفح)؛ نصوصها حُدِّثت إلى التسمية الجديدة، وتُقاس في CI.

## 5) ما يلزم قبل أول اتصال ميداني (بلا ادّعاء)

لا يوجد في هذه البيئة حساب Meta ولا حساب مزوّد مكالمات موبايل ولا رقم مرخّص، فلم يُجرَّب اتصال شبكي حقيقي.
يلزم من المشغّل: `WHATSAPP_ACCESS_TOKEN`، `WHATSAPP_PHONE_NUMBER_ID`، `WHATSAPP_APP_SECRET`،
`WHATSAPP_VERIFY_TOKEN`، و`TELEPHONY_WEBHOOK_SECRET`، `TELEPHONY_WEBHOOK_URL`، ورقم الموبايل. بلا هذه
القيم في `ENV=prod` لا تقلع العملية أصلًا.
