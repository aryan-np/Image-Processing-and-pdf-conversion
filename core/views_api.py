"""JSON API (plain Django views). Envelope {ok, error, data}."""
import json
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.conf import settings

from .models import Session, Slot, GenerationRun, ImageRun
from .presets import make_slots
from .storage import storage
from .validation import validate_upload
from .models import UploadLog

def env(data=None, ok=True, error=None, status=200):
    return JsonResponse({"ok": ok, "error": error, "data": data}, status=status)

def _body(request):
    try: return json.loads(request.body.decode() or "{}")
    except Exception: return {}

@csrf_exempt
def sessions(request):
    if request.method == "GET":
        out = []
        for s in Session.objects.all().order_by("-created_at"):
            out.append({"id": s.id, "name": s.name, "ocr_mode": s.ocr_mode,
                        "slots": s.slots.count(),
                        "filled": s.slots.exclude(media_path="").count(),
                        "final_status": s.final_status, "has_pdf": bool(s.final_media_path),
                        "is_stale": s.is_stale, "final_pages": s.final_pages,
                        "final_pdf_size": s.final_pdf_size})
        return env(out)
    if request.method == "POST":
        b = _body(request)
        s = Session.objects.create(name=b.get("name", "Untitled")[:200],
                                   ocr_mode=b.get("ocr_mode", "OFF"))
        preset = b.get("preset", "")
        if preset in ("realistic", "25", "50", "100", "200"):
            make_slots(s, preset)
        return env({"id": s.id, "name": s.name}, status=201)
    return env(None, ok=False, error="method not allowed", status=405)

@csrf_exempt
def session_detail(request, sid):
    try: s = Session.objects.get(pk=sid)
    except Session.DoesNotExist: return env(None, ok=False, error="not found", status=404)
    if request.method == "GET":
        slots = [{"id": x.id, "label": x.label, "group": x.group, "order": x.order,
                  "layout": x.layout, "required": x.required, "filled": bool(x.media_path),
                  "url": ("/media/" + x.media_path) if x.media_path else "",
                  "original_filename": x.original_filename, "w": x.width, "h": x.height,
                  "upload_status": x.upload_status, "upload_error": x.upload_error}
                 for x in s.slots.all()]
        runs = [{"id": r.id, "backend": r.backend, "ocr_mode": r.ocr_mode,
                 "total_ms": r.total_ms, "images_ms": r.images_ms, "pdf_ms": r.pdf_ms,
                 "status": r.final_status,
                 "images": [r.images_total, r.images_ok, r.images_warn, r.images_failed],
                 "pages": r.pages, "queued_at": r.queued_at.isoformat()} for r in s.gen_runs.order_by("-id")[:20]]
        pdf_url = ("/media/" + s.final_media_path) if s.final_media_path else ""
        return env({"id": s.id, "name": s.name, "ocr_mode": s.ocr_mode,
                    "final_status": s.final_status, "final_error": s.final_error,
                    "is_stale": s.is_stale, "pdf_url": pdf_url, "pdf_size": s.final_pdf_size,
                    "pages": s.final_pages, "slots": slots, "runs": runs})
    if request.method == "DELETE":
        for x in s.slots.all():
            if x.media_path: storage.delete(x.media_path)
        if s.final_media_path: storage.delete(s.final_media_path)
        s.delete()
        return env({"deleted": True})
    if request.method == "PATCH":
        b = _body(request)
        if "ocr_mode" in b and b["ocr_mode"] in ("OFF", "FALLBACK", "ALWAYS"):
            s.ocr_mode = b["ocr_mode"]
        if "name" in b: s.name = b["name"][:200]
        s.save()
        return env({"id": s.id, "ocr_mode": s.ocr_mode, "name": s.name})
    return env(None, ok=False, error="method not allowed", status=405)

@csrf_exempt
@require_http_methods(["POST"])
def add_slots(request, sid):
    try: s = Session.objects.get(pk=sid)
    except Session.DoesNotExist: return env(None, ok=False, error="not found", status=404)
    b = _body(request)
    if "bulk" in b:
        bb = b["bulk"]
        n = max(1, min(2000, int(bb.get("count", 10))))
        objs = [Slot(session=s, label=f"{bb.get('prefix','L')}{i+1:03d}",
                     group=bb.get("group", "Load"), order=s.slots.count() + i,
                     layout=bb.get("layout", "single"), required=bool(bb.get("required", True)))
                for i in range(n)]
        Slot.objects.bulk_create(objs)
        return env({"added": n})
    if "preset" in b and b["preset"] in ("realistic", "25", "50", "100", "200"):
        n = make_slots(s, b["preset"])
        return env({"added": n})
    nxt = s.slots.count()
    x = Slot.objects.create(session=s, label=b.get("label", f"DOC{nxt+1}"),
                            group=b.get("group", "General"), order=int(b.get("order", nxt)),
                            layout=b.get("layout", "single"), required=bool(b.get("required", True)))
    return env({"id": x.id}, status=201)

@csrf_exempt
def slot_detail(request, slot_id):
    from django.http import QueryDict
    try: x = Slot.objects.get(pk=slot_id)
    except Slot.DoesNotExist: return env(None, ok=False, error="not found", status=404)
    if request.method == "DELETE":
        if x.media_path: storage.delete(x.media_path)
        x.media_path = ""; x.current_url = ""; x.url_expiry_time = None
        x.upload_status = "EMPTY"; x.upload_error = ""
        sid, order = x.session_id, x.order
        x.delete()
        # renumber
        for i, y in enumerate(Slot.objects.filter(session_id=sid).order_by("order")):
            if y.order != i: y.order = i; y.save(update_fields=["order"])
        return env({"deleted": True})
    if request.method == "PATCH":
        b = _body(request)
        for f in ("label", "group", "layout", "expected_orientation"):
            if f in b: setattr(x, f, b[f])
        if "required" in b: x.required = bool(b["required"])
        if "order" in b: x.order = int(b["order"])
        if "clear" in b and b["clear"]:
            if x.media_path: storage.delete(x.media_path)
            x.media_path = ""; x.current_url = ""; x.url_expiry_time = None
            x.upload_status = "EMPTY"; x.upload_error = ""
        x.save()
        return env({"id": x.id})
    if request.method == "POST":  # action: move/clear
        b = _body(request)
        if b.get("action") == "move":
            sibs = list(Slot.objects.filter(session=x.session).order_by("order"))
            idx = [y.id for y in sibs].index(x.id)
            j = idx + int(b.get("delta", 0))
            if 0 <= j < len(sibs):
                sibs[idx], sibs[j] = sibs[j], sibs[idx]
                for i, y in enumerate(sibs):
                    y.order = i; y.save(update_fields=["order"])
            return env({"moved": True})
        if b.get("action") == "clear":
            if x.media_path: storage.delete(x.media_path)
            x.media_path = ""; x.upload_status = "EMPTY"; x.upload_error = ""
            x.save(); return env({"cleared": True})
    return env(None, ok=False, error="method not allowed", status=405)

def _store_upload(slot, data, filename):
    ok, error, measured, logs = validate_upload(data, filename)
    for lg in logs:
        UploadLog.objects.create(session=slot.session, slot_label=slot.label, check_name=lg["check"],
                                 value=lg["value"], threshold=lg["threshold"],
                                 passed=lg["passed"], duration_ms=lg["duration_ms"])
    if not ok:
        slot.upload_status = "FAILED"; slot.upload_error = error; slot.save()
        return False, error
    if slot.media_path: storage.delete(slot.media_path)
    rel = storage.save_slot_file(slot.session_id, slot.id, filename, data)
    slot.media_path = rel; slot.current_url = ""; slot.url_expiry_time = None
    slot.original_filename = filename; slot.size_bytes = len(data)
    slot.width = measured.get("w", 0); slot.height = measured.get("h", 0)
    slot.uploaded_at = timezone.now(); slot.upload_status = "OK"; slot.upload_error = ""
    slot.save()
    return True, ""

@csrf_exempt
@require_http_methods(["POST"])
def slot_upload(request, slot_id):
    try: x = Slot.objects.get(pk=slot_id)
    except Slot.DoesNotExist: return env(None, ok=False, error="not found", status=404)
    f = request.FILES.get("file")
    if not f: return env(None, ok=False, error="no file", status=400)
    ok, err = _store_upload(x, f.read(), f.name)
    if not ok: return env(None, ok=False, error=err, status=422)
    return env({"filled": True, "url": "/media/" + x.media_path})

@csrf_exempt
@require_http_methods(["POST"])
def upload_bulk(request, sid):
    try: s = Session.objects.get(pk=sid)
    except Session.DoesNotExist: return env(None, ok=False, error="not found", status=404)
    files = request.FILES.getlist("files")
    empties = list(s.slots.filter(media_path="").order_by("order"))
    res = {"assigned": 0, "errors": []}
    for f, slot in zip(files, empties):
        ok, err = _store_upload(slot, f.read(), f.name)
        if ok: res["assigned"] += 1
        else: res["errors"].append({"slot": slot.label, "error": err})
    return env(res)

@csrf_exempt
@require_http_methods(["POST"])
def fill_synthetic(request, sid):
    from pipeline.synth import generate, pick_scenario, parse_mix
    import random
    try: s = Session.objects.get(pk=sid)
    except Session.DoesNotExist: return env(None, ok=False, error="not found", status=404)
    b = _body(request)
    mix = parse_mix(b.get("mix", "clean_scan=100"))
    seed = int(b.get("seed", 1))
    only_empty = b.get("only_empty", True)
    max_n = int(b.get("count", 0) or 0)
    qs = s.slots.filter(media_path="") if only_empty else s.slots.all()
    slots = list(qs.order_by("order"))
    if max_n: slots = slots[:max_n]
    rng = random.Random(seed)
    ok_n, rej = 0, []
    for i, slot in enumerate(slots):
        sc = pick_scenario(mix, rng)
        data, ext = generate(sc, seed=seed + i, title=slot.label)
        fname = f"{slot.label}.{ext}" if sc != "corrupt_file" else f"{slot.label}.jpg"
        raw = data if isinstance(data, bytes) else data
        good, err = _store_upload(slot, raw, fname)
        if good: ok_n += 1
        else: rej.append({"slot": slot.label, "scenario": sc, "error": err})
    return env({"filled": ok_n, "rejected": rej, "total": len(slots)})

def preview_synthetic(request):
    """Live preview of one synthetic test image (GET, raw image bytes).

    Query: scenario (a SCENARIOS key or __mix__), seed, title, mix.
    scenario=__mix__ picks via the mix weights exactly like fill does, so the
    user sees what "Fill empty slots" would produce with current settings.
    Nothing is stored. corrupt_file has no preview (422).
    """
    from pipeline.synth import generate, pick_scenario, parse_mix, SCENARIOS
    import random
    sc = request.GET.get("scenario", "clean_scan")
    try:
        seed = int(request.GET.get("seed", 1))
    except (TypeError, ValueError):
        return env(None, ok=False, error="bad seed", status=400)
    title = (request.GET.get("title", "PREVIEW") or "PREVIEW")[:60]
    if sc == "__mix__":
        mix = {k: v for k, v in parse_mix(request.GET.get("mix", "clean_scan=100")).items()
               if k in SCENARIOS}
        if not mix:
            return env(None, ok=False, error="empty mix", status=400)
        sc = pick_scenario(mix, random.Random(seed))
    if sc not in SCENARIOS:
        return env(None, ok=False, error="unknown scenario", status=400)
    if sc == "corrupt_file":
        return env(None, ok=False, error="corrupt_file has no preview", status=422)
    data, ext = generate(sc, seed=seed, title=title)
    ctype = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    resp = HttpResponse(data, content_type=ctype)
    resp["X-Scenario"] = sc
    resp["Cache-Control"] = "no-store"
    return resp

@csrf_exempt
@require_http_methods(["POST"])
def generate(request, sid):
    from .services import run_generation, Conflict
    b = _body(request)
    try:
        rid = run_generation(sid, process_only=bool(b.get("process_only", False)))
        return env({"run_id": rid, "status": "PROCESSING"}, status=202)
    except Conflict:
        return env(None, ok=False, error="already PROCESSING", status=409)
    except Session.DoesNotExist:
        return env(None, ok=False, error="not found", status=404)
    except ValueError as e:
        return env(None, ok=False, error=str(e), status=422)

@csrf_exempt
@require_http_methods(["POST"])
def create_pdf(request, sid):
    """Create PDF from the last Process-Images run (no reprocessing)."""
    from .services import create_pdf as _create_pdf, Conflict
    try:
        rid = _create_pdf(sid)
        return env({"run_id": rid, "status": "PROCESSING"}, status=202)
    except Conflict:
        return env(None, ok=False, error="already PROCESSING", status=409)
    except Session.DoesNotExist:
        return env(None, ok=False, error="not found", status=404)
    except ValueError as e:
        return env(None, ok=False, error=str(e), status=422)

def run_api(request, rid):
    try: r = GenerationRun.objects.get(pk=rid)
    except GenerationRun.DoesNotExist: return env(None, ok=False, error="not found", status=404)
    from .services import _progress
    p = _progress.get(rid, {})
    return env({"id": r.id, "status": r.final_status, "total_ms": r.total_ms,
                "images_ms": r.images_ms, "pdf_ms": r.pdf_ms,
                "queue_wait_ms": r.queue_wait_ms,
                "stage_ms": r.stage_ms, "progress": p,
                "images": [r.images_total, r.images_ok, r.images_warn, r.images_failed],
                "pages": r.pages, "pdf_bytes": r.pdf_bytes})

def run_images(request, rid):
    try: r = GenerationRun.objects.get(pk=rid)
    except GenerationRun.DoesNotExist: return env(None, ok=False, error="not found", status=404)
    rows = [{"id": x.id, "slot": x.slot_label, "group": x.slot_group, "status": x.status,
             "total_ms": x.total_ms, "stage_ms": x.stage_ms, "fallback": x.fallback_used,
             "rotation": x.rotation_applied, "method": x.orientation_method,
             "skew": x.skew_angle, "deskew": x.deskew_applied, "align_method": x.align_method,
             "out": [x.out_w, x.out_h], "exception": x.exception[:200]}
            for x in r.images.order_by("slot_order")]
    return env(rows)
