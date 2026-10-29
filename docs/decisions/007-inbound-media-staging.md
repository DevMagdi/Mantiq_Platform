# 007 — الوسائط الواردة: تخزين في مستودعنا ثم ربطها بالرسالة في نفس المعاملة

**الحالة:** مقبول · **التاريخ:** 2026-10-26

## السياق
Webhook الوارد من WhatsApp/مركز الاتصال يصل بمرجع ملف (`media_id`) لا بالملف نفسه. لو مرّرنا رابط
المزوّد للواجهة فسيصبح التشغيل مشروطاً ببقاء الملف عندهم وبصلاحية رابطهم، ولما التزمت §6.4
«رابط قصير العمر + سجل تدقيق لكل تشغيل» لأن الرابط سيكون ثابتاً.

كذلك: إضافة حالة وسيطة إلى عمود `attachment_owner` كانت ستحتاج تعديل migration مدموجة (CHECK
constraint) — وممنوع تعديل migrations المدموجة (ADR-001 ثم §13).

## القرار
- `stage_inbound_media()` في `app/api/v1/endpoints/webhooks.py` هي المدخل الوحيد لأي وسيط وارد،
  وتستدعيها مساران: `POST /webhooks/whatsapp` و`POST /dev/sim/whatsapp`، فلا يختلف مسار
  المحاكي عن الإنتاج.
- النسخ يتم إلى مستودعنا بمفتاح `media/{external_id}/{media_id}` عبر `StorageProvider.put_object`
  (قرص محلي في التطوير، MinIO في الإنتاج)، مع تعقيم المعرّف بـ regex ومنعه من تجاوز 80/60 حرفاً.
- `intake.ingest_whatsapp_message()` تضيف `request_messages` و`attachments` في **نفس** المعاملة
  (`owner_type=AttachmentOwner.REQUEST`, `owner_id=request_id`)، فلا توجد صفوف يتيمة ولا حالة
  `staged` جديدة ولا migration.
- الفشل مقصود أن يكون صاخباً: إن تعذّر جلب الملف من المزوّد تُرفع الاستثناء قبل `commit`، فيُعاد
  تسليم الحدث من طرف المزوّد، وتُزال التكرارات بضمان انفرادية `external_id`.
- التشغيل عبر `GET /calls/{id}/recording-url` ورابط الوسائط الموقّع بعمر
  `RECORDING_SIGNED_URL_TTL_SECONDS` (افتراضياً 120 ثانية) مع صفّ `audit_logs` لكل تشغيل، وصلاحية
  `calls.listen` منفصلة عن `requests.view`.
- التنظيف: Celery beat — `media-retention` (حذف التسجيلات منتهية الصلاحية) يومياً 03:15،
  و`tmp-file-cleanup` لحذف `STORAGE_DIR/tmp` يومياً 04:30.

## البدائل
- تمرير رابط المزوّد مباشرة: لا خصوصية ولا تحكم في العمر، ويتوقف التشغيل على طرف ثالث.
- طابور «وسيط مؤجّل» بجدول جديد: كان سيحتاج قيد حالة جديد وmigration تعديل، مقابل فائدة لا
  تحتاجها الرسالة النصية.

## الأثر
لا يمكن أن تظهر رسالة صوتية في الواجهة بدون نسخة محلية في مستودعنا؛ و`external_id` الفريد يجعل
إعادة تسليم Webhook غير مضاعَفة. التغطية:
`tests/test_intake.py::test_inbound_whatsapp_is_idempotent_per_external_id`،
`test_inbound_whatsapp_rejects_invalid_signature`،
`test_voice_note_media_is_playable_for_staff_and_hidden_from_the_portal`،
`test_recording_url_is_permissioned_signed_and_audited`.

الفارق: لا طابور إعادة محاولة للوسائط — إن فشل الجلب من المزوّد تُرفض الاستجابة كلها ويعيد
المزوّد التسليم، ولا تُحفظ رسالة بنصف بيانات.
