import hashlib, uuid
from django.db import models
from django.utils import timezone

OCR_MODES = [("OFF", "OFF"), ("FALLBACK", "FALLBACK"), ("ALWAYS", "ALWAYS")]
FINAL_STATUS = [("NONE", "NONE"), ("PROCESSING", "PROCESSING"), ("READY", "READY"), ("FAILED", "FAILED")]

def new_uuid():
    return str(uuid.uuid4())

class Session(models.Model):
    id = models.CharField(max_length=36, primary_key=True, default=new_uuid)
    name = models.CharField(max_length=200, default="Untitled")
    created_at = models.DateTimeField(auto_now_add=True)
    ocr_mode = models.CharField(max_length=10, choices=OCR_MODES, default="OFF")
    final_media_path = models.CharField(max_length=500, blank=True, default="")
    final_current_url = models.CharField(max_length=800, blank=True, default="")
    final_url_expiry_time = models.DateTimeField(null=True, blank=True)
    final_status = models.CharField(max_length=12, choices=FINAL_STATUS, default="NONE")
    final_generated_at = models.DateTimeField(null=True, blank=True)
    final_error = models.TextField(blank=True, default="")
    final_source_hash = models.CharField(max_length=64, blank=True, default="")
    final_pdf_size = models.BigIntegerField(default=0)
    final_pages = models.IntegerField(default=0)

    def current_hash(self):
        media_paths = sorted(s.media_path for s in self.slots.all() if s.media_path)
        return hashlib.sha256("|".join(media_paths).encode()).hexdigest() if media_paths else ""

    @property
    def is_stale(self):
        if self.final_status != "READY": return False
        return (self.final_source_hash or "") != (self.current_hash() or "")

    def get_valid_url(self, ttl_hours=24):
        """Simple cached-URL helper: /media/<path> cached until expiry."""
        from django.utils import timezone as tz
        from datetime import timedelta
        now = tz.now()
        if self.final_current_url and self.final_url_expiry_time and self.final_url_expiry_time > now:
            return self.final_current_url
        from django.conf import settings
        url = settings.MEDIA_URL + (self.final_media_path or "")
        self.final_current_url = url
        self.final_url_expiry_time = now + timedelta(hours=ttl_hours)
        self.save(update_fields=["final_current_url", "final_url_expiry_time"])
        return url

class Slot(models.Model):
    LAYOUTS = [("single", "single"), ("pair", "pair")]
    session = models.ForeignKey(Session, related_name="slots", on_delete=models.CASCADE)
    label = models.CharField(max_length=120, default="DOC")
    group = models.CharField(max_length=120, default="General")
    order = models.IntegerField(default=0)
    layout = models.CharField(max_length=10, choices=LAYOUTS, default="single")
    required = models.BooleanField(default=True)
    expected_orientation = models.CharField(max_length=12, default="any")  # any|portrait|landscape
    media_path = models.CharField(max_length=500, blank=True, default="")
    current_url = models.CharField(max_length=800, blank=True, default="")
    url_expiry_time = models.DateTimeField(null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0)
    width = models.IntegerField(default=0)
    height = models.IntegerField(default=0)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    upload_status = models.CharField(max_length=10, default="EMPTY")  # EMPTY|OK|FAILED
    upload_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["order", "id"]

    def get_valid_url(self, ttl_hours=24):
        from django.utils import timezone as tz
        from datetime import timedelta
        from django.conf import settings
        now = tz.now()
        if self.current_url and self.url_expiry_time and self.url_expiry_time > now:
            return self.current_url
        self.current_url = (settings.MEDIA_URL + self.media_path) if self.media_path else ""
        self.url_expiry_time = now + timedelta(hours=ttl_hours)
        self.save(update_fields=["current_url", "url_expiry_time"])
        return self.current_url

class GenerationRun(models.Model):
    session = models.ForeignKey(Session, related_name="gen_runs", on_delete=models.CASCADE)
    backend = models.CharField(max_length=10, default="thread")
    workers = models.IntegerField(default=2)
    ocr_mode = models.CharField(max_length=10, default="OFF")
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    queue_wait_ms = models.FloatField(default=0)
    total_ms = models.FloatField(default=0)
    images_ms = models.FloatField(default=0)  # sum of per-image pipeline totals
    pdf_ms = models.FloatField(default=0)     # build_pages + merge_pdfs only (0 when process_only)
    stage_ms = models.JSONField(default=dict)  # {stage: ms}
    images_total = models.IntegerField(default=0)
    images_ok = models.IntegerField(default=0)
    images_warn = models.IntegerField(default=0)
    images_failed = models.IntegerField(default=0)
    pages = models.IntegerField(default=0)
    pdf_bytes = models.BigIntegerField(default=0)
    peak_rss = models.BigIntegerField(default=0)
    cpu_user = models.FloatField(default=0)
    cpu_sys = models.FloatField(default=0)
    settings_snapshot = models.JSONField(default=dict)
    final_status = models.CharField(max_length=12, default="PROCESSING")
    process_only = models.BooleanField(default=False)

    @property
    def total_s(self): return (self.total_ms or 0) / 1000.0

class ImageRun(models.Model):
    generation_run = models.ForeignKey(GenerationRun, related_name="images", on_delete=models.CASCADE)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    slot_label = models.CharField(max_length=120, default="")
    slot_group = models.CharField(max_length=120, default="")
    slot_order = models.IntegerField(default=0)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    source_format = models.CharField(max_length=20, blank=True, default="")
    file_size = models.BigIntegerField(default=0)
    orig_w = models.IntegerField(default=0)
    orig_h = models.IntegerField(default=0)
    exif_orientation = models.IntegerField(default=1)
    megapixels = models.FloatField(default=0)
    stage_ms = models.JSONField(default=dict)
    total_ms = models.FloatField(default=0)
    area_ratio = models.FloatField(default=0)
    detected_angle = models.FloatField(default=0)
    second_contour_ratio = models.FloatField(default=0)
    quad_confidence = models.FloatField(default=0)
    fallback_used = models.CharField(max_length=30, blank=True, default="")
    rotation_applied = models.IntegerField(default=0)
    orientation_method = models.CharField(max_length=30, blank=True, default="")
    osd_confidence = models.FloatField(null=True, blank=True)
    skew_angle = models.FloatField(default=0)
    deskew_applied = models.FloatField(default=0)
    align_method = models.CharField(max_length=30, blank=True, default="")
    out_w = models.IntegerField(default=0)
    out_h = models.IntegerField(default=0)
    out_jpeg_bytes = models.IntegerField(default=0)
    rss_delta = models.BigIntegerField(default=0)
    alloc_peak = models.BigIntegerField(default=0)
    thread_id = models.CharField(max_length=60, blank=True, default="")
    status = models.CharField(max_length=10, default="OK")
    warnings = models.JSONField(default=list)
    exception = models.TextField(blank=True, default="")
    steps = models.JSONField(default=list)  # human-readable per-stage log lines

class OcrLog(models.Model):
    image_run = models.ForeignKey(ImageRun, related_name="ocr_logs", null=True, blank=True, on_delete=models.CASCADE)
    generation_run = models.ForeignKey(GenerationRun, related_name="ocr_logs", null=True, blank=True, on_delete=models.CASCADE)
    session = models.ForeignKey(Session, related_name="ocr_logs", on_delete=models.CASCADE)
    slot_label = models.CharField(max_length=120, default="")
    reason = models.CharField(max_length=20, default="")  # FALLBACK|ALWAYS
    tesseract_version = models.CharField(max_length=60, blank=True, default="")
    languages = models.CharField(max_length=60, default="eng")
    config = models.CharField(max_length=200, blank=True, default="")
    input_w = models.IntegerField(default=0)
    input_h = models.IntegerField(default=0)
    raw_osd = models.TextField(blank=True, default="")
    osd_rotate = models.IntegerField(default=0)
    osd_confidence = models.FloatField(default=-1)
    osd_script = models.CharField(max_length=40, blank=True, default="")
    osd_script_conf = models.FloatField(default=-1)
    rotation_applied = models.IntegerField(default=0)
    full_text = models.TextField(blank=True, default="")
    words = models.JSONField(default=list)  # [{text, conf, bbox}]
    mean_conf = models.FloatField(default=-1)
    min_conf = models.FloatField(default=-1)
    word_count = models.IntegerField(default=0)
    duration_ms = models.FloatField(default=0)
    cpu_ms = models.FloatField(default=0)
    success = models.BooleanField(default=True)
    exception = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

class UploadLog(models.Model):
    session = models.ForeignKey(Session, related_name="upload_logs", on_delete=models.CASCADE)
    slot_label = models.CharField(max_length=120, default="")
    check_name = models.CharField(max_length=40, default="")
    value = models.CharField(max_length=200, blank=True, default="")
    threshold = models.CharField(max_length=200, blank=True, default="")
    passed = models.BooleanField(default=True)
    duration_ms = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
