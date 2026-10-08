from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.db.models import Q
import statistics

from .models import Session, Slot, GenerationRun, ImageRun, OcrLog, UploadLog

def index(request):
    sessions = Session.objects.all().order_by("-created_at")
    rows = []
    for s in sessions:
        rows.append({"s": s, "n": s.slots.count(),
                     "filled": s.slots.exclude(media_path="").count()})
    return render(request, "index.html", {"rows": rows})

def workspace(request, sid):
    s = get_object_or_404(Session, pk=sid)
    from pipeline.synth import SCENARIOS, SCENARIO_META
    scenarios = [{"key": k, "label": SCENARIO_META.get(k, (k, False))[0],
                  "rejects": SCENARIO_META.get(k, (k, False))[1]} for k in SCENARIOS]
    return render(request, "workspace.html", {"s": s, "scenarios": scenarios})

def fmt_ms(ms):
    """Human timing: 350ms, 1.2s, 2m 05s."""
    try: ms = float(ms or 0)
    except (TypeError, ValueError): return "—"
    if ms < 1000: return f"{ms:.0f}ms"
    s = ms / 1000
    if s < 60: return f"{s:.1f}s"
    return f"{int(s // 60)}m {int(s % 60):02d}s"

def run_detail(request, rid):
    r = get_object_or_404(GenerationRun, pk=rid)
    imgs = list(r.images.order_by("slot_order"))
    n = max(1, r.images_total)
    timing = {"total": fmt_ms(r.total_ms), "images": fmt_ms(r.images_ms),
              "pdf": fmt_ms(r.pdf_ms), "queue": fmt_ms(r.queue_wait_ms),
              "per_image": fmt_ms(r.images_ms / n)}
    # original vs processed comparison: originals from current slot files,
    # processed from this run's process cache (only when it is this run's own).
    from .storage import storage
    slot_by_order = {s.order: s for s in r.session.slots.all()}
    manifest, _ = storage.load_process_cache(r.session_id)
    proc_by_order = {}
    if manifest and manifest.get("run_id") == r.id:
        for it in manifest.get("items", []):
            proc_by_order[it["order"]] = "/media/_process_cache/%s/%s" % (
                r.session_id, it["file"])
    compare = []
    for img in imgs:
        slot = slot_by_order.get(img.slot_order)
        orig = ""
        if slot and slot.media_path and storage.exists(slot.media_path):
            orig = "/media/" + slot.media_path
        compare.append({"label": img.slot_label, "group": img.slot_group,
                        "status": img.status, "orig": orig,
                        "proc": proc_by_order.get(img.slot_order, "")})
    return render(request, "run_detail.html",
                  {"r": r, "imgs": imgs, "timing": timing, "compare": compare})

def image_detail(request, rid, iid):
    r = get_object_or_404(GenerationRun, pk=rid)
    img = get_object_or_404(ImageRun, pk=iid, generation_run=r)
    ocrs = img.ocr_logs.all()
    # reconstruct processed preview on demand (re-run pipeline for that single image)
    preview_url = None
    try:
        from .storage import storage
        data = storage.read(r.session.slots.filter(label=img.slot_label).first().media_path) \
            if r.session.slots.filter(label=img.slot_label).exists() else None
    except Exception:
        data = None
    return render(request, "image_detail.html", {"r": r, "img": img, "ocrs": ocrs})

def ocr_list(request):
    qs = OcrLog.objects.select_related("session").order_by("-id")
    run = request.GET.get("run"); slot = request.GET.get("slot"); reason = request.GET.get("reason")
    if run: qs = qs.filter(generation_run_id=run)
    if slot: qs = qs.filter(slot_label__icontains=slot)
    if reason: qs = qs.filter(reason=reason)
    return render(request, "ocr_list.html", {"logs": qs[:300]})

def ocr_detail(request, oid):
    o = get_object_or_404(OcrLog, pk=oid)
    return render(request, "ocr_detail.html", {"o": o})

def _pct(xs, p):
    if not xs: return 0
    xs = sorted(xs); k = (len(xs) - 1) * p / 100
    f, c = int(k), min(len(xs) - 1, int(k) + 1)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 2)

def analysis(request):
    runs = GenerationRun.objects.select_related("session").order_by("-id")[:200]
    ocr = request.GET.get("ocr"); slots = request.GET.get("slots")
    if ocr: runs = runs.filter(ocr_mode=ocr)
    runs = list(runs)
    if slots:
        runs = [r for r in runs if r.images_total == int(slots)]
    for r in runs:
        n = max(1, r.images_total)
        r.per_image = fmt_ms((r.images_ms or 0) / n)
        r.total_h = fmt_ms(r.total_ms)
        r.images_h = fmt_ms(r.images_ms)
        r.pdf_h = fmt_ms(r.pdf_ms)
    return render(request, "analysis.html",
                  {"runs": runs, "f": {"ocr": ocr or "", "slots": slots or ""}})

def analysis_export(request, fmt):
    import json, csv
    runs = GenerationRun.objects.all().order_by("id")
    rows = [{"id": r.id, "session": r.session_id, "backend": r.backend, "ocr": r.ocr_mode,
             "slots": r.images_total, "ok": r.images_ok, "warn": r.images_warn, "failed": r.images_failed,
             "total_ms": r.total_ms, "images_ms": r.images_ms, "pdf_ms": r.pdf_ms,
             "stage_ms": r.stage_ms, "pages": r.pages, "pdf_bytes": r.pdf_bytes,
             "peak_rss": r.peak_rss, "status": r.final_status} for r in runs]
    if fmt == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = "attachment; filename=analysis.csv"
        w = csv.DictWriter(resp, fieldnames=["id", "session", "backend", "ocr", "slots", "ok", "warn",
                                             "failed", "total_ms", "images_ms", "pdf_ms", "stage_ms",
                                             "pages", "pdf_bytes", "peak_rss", "status"])
        w.writeheader(); w.writerows([{**x, "stage_ms": str(x["stage_ms"])} for x in rows])
        return resp
    return JsonResponse({"ok": True, "error": None, "data": rows})

def settings_view(request):
    import django, sys, platform, PIL, cv2, numpy
    import psutil
    from pipeline.ocr import tesseract_version
    fb_on = getattr(settings, "OCR_FALLBACK_ENABLED", True)
    fb_effective = bool(settings.TESSERACT_AVAILABLE and fb_on)
    # Each section: heading + rows(label, env key, value, short hint).
    sections = [
        {"heading": "Upload validation",
         "blurb": "Checked on every upload. Rejected files show the reason on the slot.",
         "rows": [
            ("Max file size", "UPLOAD_MAX_MB", f"{settings.UPLOAD_MAX_MB} MB", "larger files are rejected"),
            ("Min image size", "UPLOAD_MIN_SHORT_SIDE", f"{settings.UPLOAD_MIN_SHORT_SIDE} px", "shorter side must be at least this"),
            ("Max image size", "UPLOAD_MAX_MEGAPIXELS", f"{settings.UPLOAD_MAX_MEGAPIXELS} MP", "bigger images are rejected"),
            ("Blur limit", "UPLOAD_BLUR_THRESHOLD", settings.UPLOAD_BLUR_THRESHOLD, "lower = blurrier images rejected"),
            ("Allow WebP", "UPLOAD_ALLOW_WEBP", settings.UPLOAD_ALLOW_WEBP, "jpg / jpeg / png always allowed"),
            ("Allow HEIC", "UPLOAD_ALLOW_HEIC", settings.UPLOAD_ALLOW_HEIC, "needs pillow-heif installed"),
        ]},
        {"heading": "Image processing",
         "blurb": "How each image is cleaned up before going into the PDF.",
         "rows": [
            ("Enhance (contrast)", "ENHANCE_ENABLED", settings.ENHANCE_ENABLED, "gentle CLAHE, no binarization"),
        ]},
        {"heading": "OCR",
         "blurb": "Text recognition is optional. FALLBACK runs only when orientation is unclear.",
         "rows": [
            ("Tesseract installed", "TESSERACT_AVAILABLE", settings.TESSERACT_AVAILABLE, f"version {tesseract_version() or '—'}"),
            ("OCR languages", "OCR_LANGUAGES", settings.OCR_LANGUAGES, "e.g. eng+nep if installed"),
            ("Fallback OCR allowed", "OCR_FALLBACK_ENABLED", fb_on, "off makes FALLBACK behave like OFF"),
            ("Fallback effectively", "—", "active" if fb_effective else "gated off", "needs tesseract + toggle on"),
        ]},
        {"heading": "Generation",
         "blurb": "How PDF jobs run in the background.",
         "rows": [
            ("Backend", "GEN_BACKEND", settings.GEN_BACKEND, "thread / celery / sync"),
            ("Workers", "GEN_WORKERS", settings.GEN_WORKERS, "parallel image workers"),
            ("Time limit", "GEN_TIMELIMIT_SECONDS", f"{settings.GEN_TIMELIMIT_SECONDS} s", "max seconds per job"),
        ]},
        {"heading": "System",
         "blurb": "Included in every report for reproducibility.",
         "rows": [
            ("Python", "—", sys.version.split()[0], platform.platform()),
            ("Django", "—", django.__version__, ""),
            ("Pillow / OpenCV / NumPy", "—",
             f"{PIL.__version__} / {cv2.__version__} / {numpy.__version__}", ""),
            ("CPU / RAM", "—",
             f"{__import__('os').cpu_count()} cores / {round(psutil.virtual_memory().total / 1e9, 1)} GB", ""),
        ]},
    ]
    return render(request, "settings.html", {"sections": sections})
