"""Generation orchestration: run_generation(session_id) behind GEN_BACKEND."""
import concurrent.futures, json, logging, os, threading, time, traceback
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from pipeline.runner import process_image, jsonl_append
from pipeline.enhance_compose_merge import build_pages, merge_pdfs

log = logging.getLogger("slotlab.gen")
_executor = None
_lock = threading.Lock()
_progress = {}  # gen_run_id -> {"done": int, "total": int, "tail": [str]}

def get_executor():
    global _executor
    with _lock:
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, settings.GEN_WORKERS))
        return _executor

def _tail_append(run_id, line, cap=300):
    """Thread-safe append of a human-readable processing line for the live log."""
    with _lock:
        t = _progress.get(run_id)
        if t is None:
            return
        tail = t.setdefault("tail", [])
        tail.append(line)
        if len(tail) > cap:
            del tail[:len(tail) - cap]
    global _executor
    with _lock:
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, settings.GEN_WORKERS))
        return _executor

def _settings_snapshot():
    return {"UPLOAD_MAX_MB": settings.UPLOAD_MAX_MB, "UPLOAD_MIN_SHORT_SIDE": settings.UPLOAD_MIN_SHORT_SIDE,
            "UPLOAD_MAX_MEGAPIXELS": settings.UPLOAD_MAX_MEGAPIXELS, "UPLOAD_BLUR_THRESHOLD": settings.UPLOAD_BLUR_THRESHOLD,
            "ENHANCE_ENABLED": settings.ENHANCE_ENABLED, "OCR_LANGUAGES": settings.OCR_LANGUAGES,
            "OCR_FALLBACK_ENABLED": getattr(settings, "OCR_FALLBACK_ENABLED", True),
            "GEN_WORKERS": settings.GEN_WORKERS, "GEN_BACKEND": settings.GEN_BACKEND}

def _cpu_times():
    try:
        import psutil
        p = psutil.Process()
        t = p.cpu_times()
        return t.user, t.system, p.memory_info().rss
    except Exception:
        return 0, 0, 0

def run_generation(session_id, process_only=False):
    """Public interface. Dispatch by GEN_BACKEND. Returns GenerationRun id."""
    from .models import Session, GenerationRun
    backend = getattr(settings, "GEN_BACKEND", "thread")
    with transaction.atomic():
        sess = Session.objects.select_for_update().get(pk=session_id)
        if sess.final_status == "PROCESSING":
            raise Conflict("already PROCESSING")
        missing = [s.label for s in sess.slots.all() if s.required and not s.media_path]
        if missing:
            raise ValueError("required slots empty: " + ", ".join(missing[:10]))
        run = GenerationRun.objects.create(session=sess, backend=backend,
                                           workers=getattr(settings, "GEN_WORKERS", 2),
                                           ocr_mode=sess.ocr_mode,
                                           settings_snapshot=_settings_snapshot(),
                                           process_only=process_only)
        sess.final_status = "PROCESSING"; sess.final_error = ""
        sess.save(update_fields=["final_status", "final_error"])
    _progress[run.id] = {"done": 0, "total": sess.slots.filter(media_path__gt="").count(),
                         "tail": [], "started": time.time()}
    if backend == "sync":
        run_generation_sync(run.id)
    elif backend == "celery":
        try:
            from slotlab.celery_app import generate_task
            if generate_task is None: raise ImportError("celery not installed")
            generate_task.delay(run.id)
        except Exception:
            get_executor().submit(run_generation_sync, run.id)
    else:
        get_executor().submit(run_generation_sync, run.id)
    return run.id

def _dispatch(run_id, sync_fn):
    """Run inline for sync backend, else on the shared pool / celery."""
    backend = getattr(settings, "GEN_BACKEND", "thread")
    if backend == "sync":
        sync_fn(run_id)
    elif backend == "celery":
        try:
            from slotlab.celery_app import generate_task
            if generate_task is None: raise ImportError("celery not installed")
            generate_task.delay(run_id)
        except Exception:
            get_executor().submit(sync_fn, run_id)
    else:
        get_executor().submit(sync_fn, run_id)

def create_pdf(session_id):
    """Build the final PDF from the last Process-Images run (no reprocessing).
    Raises ValueError if slots changed since processing. Returns GenerationRun id."""
    from .models import Session, GenerationRun
    from .storage import storage
    backend = getattr(settings, "GEN_BACKEND", "thread")
    with transaction.atomic():
        sess = Session.objects.select_for_update().get(pk=session_id)
        if sess.final_status == "PROCESSING":
            raise Conflict("already PROCESSING")
        manifest, _ = storage.load_process_cache(session_id)
        if not manifest or manifest.get("source_hash") != sess.current_hash():
            raise ValueError("images changed since processing — press Process Images first")
        snap = _settings_snapshot()
        snap["reused_process_run"] = manifest.get("run_id")
        run = GenerationRun.objects.create(session=sess, backend=backend,
                                           workers=getattr(settings, "GEN_WORKERS", 2),
                                           ocr_mode=sess.ocr_mode,
                                           settings_snapshot=snap, process_only=False)
        sess.final_status = "PROCESSING"; sess.final_error = ""
        sess.save(update_fields=["final_status", "final_error"])
    _progress[run.id] = {"done": 0, "total": len(manifest.get("items", [])),
                         "tail": [], "started": time.time()}
    _dispatch(run.id, run_create_pdf_sync)
    return run.id

def run_create_pdf_sync(gen_run_id, backend=None):
    """Assemble the PDF from cached processed images. Fast: no image pipeline."""
    from .models import GenerationRun
    from .storage import storage
    t_start = time.perf_counter()
    run = GenerationRun.objects.get(pk=gen_run_id)
    sess = run.session
    run.started_at = timezone.now()
    run.queue_wait_ms = (run.started_at - run.queued_at).total_seconds() * 1000
    if backend: run.backend = backend
    run.save(update_fields=["started_at", "queue_wait_ms", "backend"])
    status, err, pdf_bytes, pages, n = "READY", "", b"", 0, 0
    t_pdf = time.perf_counter()
    try:
        manifest, cdir = storage.load_process_cache(sess.id)
        if not manifest or manifest.get("source_hash") != sess.current_hash():
            raise ValueError("images changed since processing — press Process Images first")
        items = []
        for it in sorted(manifest["items"], key=lambda x: x["order"]):
            jpeg = (cdir / it["file"]).read_bytes()
            items.append({"jpeg": jpeg, "caption": it["label"],
                          "group": it["group"], "layout": it["layout"]})
        n = len(items)
        if not n:
            raise ValueError("no processed images cached")
        pages_pdf = build_pages(items, sess.name)
        pdf_bytes, pages = merge_pdfs(pages_pdf)
        if sess.final_media_path: storage.delete(sess.final_media_path)
        sess.final_media_path = storage.save_final_pdf(sess.id, run.id, pdf_bytes)
        sess.final_current_url = ""
        sess.final_url_expiry_time = None
        sess.final_source_hash = sess.current_hash()
        sess.final_pdf_size = len(pdf_bytes)
        sess.final_pages = pages
        sess.final_generated_at = timezone.now()
        sess.final_error = ""
        sess.final_status = "READY"
        sess.save()
        run.images_total = n; run.images_ok = n
        run.pages, run.pdf_bytes = pages, len(pdf_bytes)
    except Exception as e:
        status, err = "FAILED", f"{type(e).__name__}: {e}"[:500]
        log.exception("create-pdf %s failed", gen_run_id)
        sess.final_status = "FAILED"; sess.final_error = err; sess.save()
    finally:
        run.finished_at = timezone.now()
        run.total_ms = (time.perf_counter() - t_start) * 1000
        run.images_ms = 0
        run.pdf_ms = round((time.perf_counter() - t_pdf) * 1000, 2) if status == "READY" else 0
        run.final_status = status
        run.save()
        _progress[gen_run_id]["done"] = _progress[gen_run_id]["total"]
        jsonl_append({"ts": timezone.now().isoformat(), "run": str(gen_run_id),
                      "slot": "__summary__", "stage": "__pdf_only__", "status": status,
                      "total_ms": round(run.total_ms, 1), "pdf_ms": run.pdf_ms, "pages": pages})
        log.info(f"run={gen_run_id} stage=__pdf_only__ ms={run.total_ms:.0f} "
                 f"pdf_ms={run.pdf_ms:.0f} status={status} pages={pages}")
    return status

class Conflict(Exception): pass

def run_generation_sync(gen_run_id, backend=None):
    from .models import Session, Slot, GenerationRun, ImageRun, OcrLog
    from .storage import storage
    t_start = time.perf_counter()
    run = GenerationRun.objects.get(pk=gen_run_id)
    sess = run.session
    run.started_at = timezone.now()
    run.queue_wait_ms = (run.started_at - run.queued_at).total_seconds() * 1000
    if backend: run.backend = backend
    run.save(update_fields=["started_at", "queue_wait_ms", "backend"])
    cpu_u0, cpu_s0, _ = _cpu_times()
    deadline = t_start + getattr(settings, "GEN_TIMELIMIT_SECONDS", 120)
    images, stage_totals = [], {s: 0 for s in ["load", "detect", "crop_deskew", "orientation", "align", "enhance", "compose"]}
    status, err = "READY", ""
    pdf_bytes, pages = b"", 0
    try:
        from pipeline.ocr import tesseract_available as _tess_ok
        _has_ocr = _tess_ok()
    except Exception:
        _has_ocr = False
    _tail_append(gen_run_id,
                 f"Run {gen_run_id}: OCR mode {run.ocr_mode} · "
                 + ("tesseract found — sideways/upside-down pages will be auto-rotated"
                    if _has_ocr and run.ocr_mode in ("FALLBACK", "ALWAYS") else
                    "tesseract MISSING — install it or rotations stay as-is" if not _has_ocr else
                    "OCR OFF — sideways/upside-down pages will NOT be rotated"))
    try:
        slots = list(sess.slots.filter(media_path__gt="").order_by("order"))
        run.images_total = len(slots)
        run.save(update_fields=["images_total"])
        _progress[gen_run_id]["total"] = len(slots)

        def one(slot):
            data = storage.read(slot.media_path)
            jpeg, m, ocr_events = process_image(
                data, run_id=gen_run_id, slot_label=slot.label, slot_group=slot.group,
                slot_order=slot.order, original_filename=slot.original_filename,
                ocr_mode=run.ocr_mode, ocr_languages=run.settings_snapshot.get("OCR_LANGUAGES", "eng"),
                ocr_fallback_enabled=run.settings_snapshot.get("OCR_FALLBACK_ENABLED", True),
                expected_orientation=slot.expected_orientation,
                enhance_enabled=run.settings_snapshot.get("ENHANCE_ENABLED", True),
                log_cb=lambda stage, line: _tail_append(gen_run_id, line))
            return slot, jpeg, m, ocr_events

        # bounded pool inside sync too (workers setting), but deterministic order preserved
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, run.workers)) as pool:
            futs = {pool.submit(one, s): s for s in slots}
            ordered = {}
            for fut in concurrent.futures.as_completed(futs, timeout=max(5, getattr(settings, "GEN_TIMELIMIT_SECONDS", 120))):
                if time.perf_counter() > deadline:
                    raise TimeoutError("generation time limit exceeded")
                slot = futs[fut]
                try:
                    slot, jpeg, m, ocr_events = fut.result()
                    ordered[slot.order] = (slot, jpeg, m, ocr_events, None)
                except Exception as e:
                    ordered[futs[fut].order] = (futs[fut], b"", {}, [], f"{e}"[:500])
                _progress[gen_run_id]["done"] += 1
        # persist in slot order
        items = []
        ok = warn = fail = 0
        cache_items = []
        for order in sorted(ordered):
            slot, jpeg, m, ocr_events, exc = ordered[order]
            if exc:
                ImageRun.objects.create(generation_run=run, session=sess, slot_label=slot.label,
                                        slot_group=slot.group, slot_order=slot.order,
                                        original_filename=slot.original_filename, status="FAILED",
                                        exception=exc, stage_ms={}, total_ms=0)
                fail += 1
                continue
            for k, v in (m.get("stage_ms") or {}).items():
                stage_totals[k] = stage_totals.get(k, 0) + v
            irst = ImageRun.objects.create(
                generation_run=run, session=sess, slot_label=slot.label, slot_group=slot.group,
                slot_order=slot.order, original_filename=slot.original_filename,
                source_format=m.get("source_format", ""), file_size=slot.size_bytes,
                orig_w=m.get("orig_w", 0), orig_h=m.get("orig_h", 0),
                exif_orientation=m.get("exif_orientation", 1), megapixels=m.get("megapixels", 0),
                stage_ms=m.get("stage_ms", {}), total_ms=m.get("total_ms", 0),
                area_ratio=m.get("area_ratio", 0), detected_angle=m.get("detected_angle", 0),
                second_contour_ratio=m.get("second_ratio", 0), quad_confidence=m.get("quad_confidence", 0),
                fallback_used=m.get("fallback", ""), rotation_applied=m.get("rotation", 0),
                orientation_method=m.get("orientation_method", ""), osd_confidence=m.get("osd_confidence"),
                skew_angle=m.get("skew_angle", 0), deskew_applied=m.get("deskew_deg", 0),
                align_method=m.get("align_method", ""),
                out_w=m.get("out_w", 0), out_h=m.get("out_h", 0),
                out_jpeg_bytes=m.get("jpeg_bytes", 0), rss_delta=m.get("rss_delta", 0),
                alloc_peak=m.get("alloc_peak", 0), thread_id=m.get("thread", ""),
                status=m.get("status", "OK"), warnings=m.get("warnings", []), exception="",
                steps=m.get("steps", []))
            if m.get("status") == "OK": ok += 1
            elif m.get("status") == "WARN": warn += 1
            else: fail += 1
            for evt in ocr_events:
                OcrLog.objects.create(image_run=irst, generation_run=run, session=sess,
                                      slot_label=slot.label, reason=evt.get("reason", ""),
                                      tesseract_version=evt.get("tesseract_version", ""),
                                      languages=evt.get("languages", "eng"), config=evt.get("config", ""),
                                      input_w=(evt.get("input_size") or [0, 0])[0],
                                      input_h=(evt.get("input_size") or [0, 0])[1],
                                      raw_osd=evt.get("raw_osd", ""), osd_rotate=evt.get("rotate", 0),
                                      osd_confidence=evt.get("confidence", -1),
                                      osd_script=evt.get("script", ""), osd_script_conf=evt.get("script_conf", -1),
                                      rotation_applied=m.get("rotation", 0),
                                      full_text=evt.get("full_text", ""), words=evt.get("words", []),
                                      mean_conf=evt.get("mean_conf", -1), min_conf=evt.get("min_conf", -1),
                                      word_count=evt.get("word_count", 0),
                                      duration_ms=evt.get("duration_ms", 0), cpu_ms=evt.get("cpu_ms", 0),
                                      success=evt.get("success", True), exception=evt.get("exception", ""))
                jsonl_append({"ts": timezone.now().isoformat(), "run": str(gen_run_id),
                              "slot": slot.label, "stage": "ocr", "reason": evt.get("reason"),
                              "ms": evt.get("duration_ms"), "rotate": evt.get("rotate"),
                              "conf": evt.get("confidence"), "words": evt.get("word_count"),
                              "text": (evt.get("full_text") or "")[:500]})
            items.append({"jpeg": jpeg, "caption": slot.label, "group": slot.group, "layout": slot.layout})
            cache_items.append({"order": slot.order, "label": slot.label,
                                "group": slot.group, "layout": slot.layout, "jpeg": jpeg})
        if fail and (ok + warn == 0):
            raise RuntimeError(f"all {fail} images failed")
        pdf_ms = 0.0
        if not run.process_only:
            t_pdf = time.perf_counter()
            pages_pdf = build_pages(items, sess.name)
            pdf_bytes, pages = merge_pdfs(pages_pdf)
            pdf_ms = (time.perf_counter() - t_pdf) * 1000
            # delete old pdf, write new
            if sess.final_media_path: storage.delete(sess.final_media_path)
            rel = storage.save_final_pdf(sess.id, run.id, pdf_bytes)
            sess.final_media_path = rel
            sess.final_current_url = ""
            sess.final_url_expiry_time = None
            sess.final_source_hash = sess.current_hash()
            sess.final_pdf_size = len(pdf_bytes)
            sess.final_pages = pages
        else:
            pages = len(items)
        run.images_ok, run.images_warn, run.images_failed = ok, warn, fail
        run.pages, run.pdf_bytes = pages, len(pdf_bytes)
        run.images_ms = round(sum(stage_totals.values()), 2)
        run.pdf_ms = round(pdf_ms, 2)
        if status == "READY" and cache_items:
            # remember processed images so "Create PDF" can run as a separate step
            try:
                storage.save_process_cache(sess.id, sess.current_hash(), run.id, cache_items)
            except Exception:
                log.exception("process cache write failed")
    except Exception as e:
        status, err = "FAILED", f"{type(e).__name__}: {e}"[:500]
        log.exception("generation %s failed", gen_run_id)
    finally:
        cpu_u1, cpu_s1, rss = _cpu_times()
        run.finished_at = timezone.now()
        run.total_ms = (time.perf_counter() - t_start) * 1000
        run.stage_ms = {k: round(v, 2) for k, v in stage_totals.items()}
        run.peak_rss, run.cpu_user, run.cpu_sys = rss, cpu_u1 - cpu_u0, cpu_s1 - cpu_s0
        run.final_status = status
        run.save()
        sess.final_status = status
        if status == "READY":
            sess.final_generated_at = timezone.now(); sess.final_error = ""
        else:
            sess.final_error = err
        sess.save()
        jsonl_append({"ts": timezone.now().isoformat(), "run": str(gen_run_id),
                      "slot": "__summary__", "stage": "__run_done__", "status": status,
                      "total_ms": round(run.total_ms, 1), "images_ms": run.images_ms,
                      "pdf_ms": run.pdf_ms, "stages": run.stage_ms,
                      "images": [run.images_total, run.images_ok, run.images_warn, run.images_failed],
                      "pages": run.pages, "error": err})
        log.info(f"run={gen_run_id} stage=__run_done__ ms={run.total_ms:.0f} "
                 f"images_ms={run.images_ms:.0f} pdf_ms={run.pdf_ms:.0f} status={status} pages={run.pages}")
        _progress[gen_run_id]["done"] = _progress[gen_run_id]["total"]
    return status
