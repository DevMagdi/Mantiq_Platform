# 011 — `/metrics` وmailpit والمحاكيات: ما هو خدمة وما هو داخل العملية

**الحالة:** مقبول · **التاريخ:** 2026-10-26 · **مرتبط بـ:** §3 (البنية), §10 (المراقبة), §8 (المحاكيات إلزامية)

## السياق
§10 يطلب structlog JSON مع correlation id ومقاييس Prometheus أساسية، و§3 يعدّد في compose خدمة
mailpit «ومحاكيين للواتساب والمكالمات». السابق لم يكن فيه `/metrics` ولا mailpit، والمحاكيات كانت
داخل الـAPI فقط.

## القرار
1. **`GET /metrics` (جذر المسار، مثل `/healthz`)** يعرّض ثلاث سلاسل فقط:
   `broker_hub_http_requests_total{method,route,status}`، `broker_hub_http_duration_seconds{method,route}`،
   `broker_hub_http_in_flight_requests` + `broker_hub_process_uptime_seconds`.
   التسمية `route` هي **قالب المسار** من FastAPI لا الـURL الخام، حتى لا يصنع مسار مجهول عدداً غير محدود
   من السلاسل (الحماية القياسية من cardinality explosion).
2. **البوابة:** إن كانت `METRICS_TOKEN` مضبوطة يُطلب `Authorization: Bearer <token>`؛ وإن لم تكن مضبوطة
   تُعرض السلاسل في التطوير فقط (`DEBUG=true`) وترجع 403 `metrics_disabled` في الإنتاج. لا نكشف
   مسارات داخلية للعامة افتراضياً.
3. **مقاييس الـworker غير موجودة عمداً:** Sweep الـSLA ومهام الاستيراد تعمل في عملية Celery، وأي Gauge
   يُكتب هناك لن يقرأه الماسح من عملية الـAPI أبداً — فكان الأفضل سجلات structlog + توثيق الفارق بدل
   سلاسل تبدو صحيحة وتقرأ صفراً. الربط عبر pushgateway/pushgateway-less exporter قرار لاحق.
4. **mailpit في compose** (`axllent/mailpit:v1.20`) على :1025 (SMTP) و:8025 (واجهة)، مع `SMTP_URL`
   و`EMAIL_FROM` في متغيرات بيئة الخادم: كل إشعار بريدّي (تحذير SLA، تذكير متابعة، رد على طلب) يصل
   محلياً بدون أي مزوّد، ولا شيء يغادر الجهاز.
5. **المحاكيات تبقى داخل عملية الـAPI** (`/api/v1/dev/sim/*` + `GET /dev/sim`) ولا تُضاف كخدمتين منفصلتين:
   هي تحتاج نفس طبقة البيانات التي يبنيها الـintake، وفصلها كان سيكرّر منطق التطبيع والمطابقة
   (`normalize_mobile` ← `insured_persons` ← `leads`) في مكانين. لوحة `/dev/sim` + `scripts/smoke_api.py`
   يغطيان الاستخدام اليدآلي والـCLI على حد سواء، وكلاهما ممنوع عند `INTEGRATIONS_DRIVER=live` (403).

## الأثر
`tests/test_metrics.py` يغطي التسمية بقالب المسار، ومنع تسرب مسارات غير معروفة كسلاسل، ووضع التوكن
(401) والإنتاج بلا توكن (403). `infra/README.md` يوثّق منفذي mailpit، و`docs/runbook.md` يضيف الصفتين
إلى جدول المراقبة.
