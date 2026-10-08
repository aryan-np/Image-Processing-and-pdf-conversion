"""Runner: process one image through timed stages. Django-free (callbacks for OCR/DB)."""
import io, json, logging, os, threading, time, tracemalloc
from datetime import datetime, timezone

from .load import stage_load
from .detect import stage_detect
from .crop import stage_crop_deskew
from .orient import stage_orientation
from .align import stage_align
from .enhance_compose_merge import stage_enhance, stage_compose
from .ocr import osd_info, full_ocr, tesseract_available, tesseract_version

log = logging.getLogger("slotlab.pipeline")
STAGES = ["load", "detect", "crop_deskew", "orientation", "align", "enhance", "compose"]

def _rss():
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except Exception:
        return 0

def _now_ms(t0): return (time.perf_counter() - t0) * 1000

def jsonl_append(payload, log_path="logs/pipeline.jsonl"):
    try:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass

def process_image(data: bytes, *, run_id="?", slot_label="", slot_group="", slot_order=0,
                   original_filename="", ocr_mode="OFF", ocr_languages="eng",
                   ocr_fallback_enabled=True,
                   expected_orientation="any", enhance_enabled=True, log_path="logs/pipeline.jsonl",
                   collect_ocr_detail=True, ocr_reason_cb=None, log_cb=None):
    """Returns (jpeg_bytes, image_metrics, ocr_events). ocr_events: list of dicts for caller to persist.

    ocr_fallback_enabled mirrors the OCR_FALLBACK_ENABLED env toggle: when False,
    FALLBACK mode never invokes OCR (zero ocr_events, heuristic orientation only).

    log_cb(stage, entry): optional callback receiving a structured step entry
    {"slot", "stage", "msg", "ms"} for every step (also collected in
    metrics["steps"]). The web UI renders these as a pretty per-photo,
    per-step log with millisecond timings. Never breaks the pipeline if it raises.
    """
    tracemalloc.start()
    rss0 = _rss()
    stage_ms, m, warnings, ocr_events, steps = {}, {}, [], [], []
    status, exc = "OK", ""
    t_all = time.perf_counter()
    cpu0 = time.process_time()
    tag = slot_label or f"#{slot_order}"

    def emit(stage, msg, ms=None):
        entry = {"slot": tag, "stage": stage, "msg": msg,
                 "ms": None if ms is None else round(float(ms), 1)}
        steps.append(entry)
        try:
            if log_cb is not None:
                log_cb(stage, entry)
        except Exception:
            pass

    def timed(name, fn, *a, **k):
        t0 = time.perf_counter()
        try:
            out, mm = fn(*a, **k)
            dt = _now_ms(t0)
            stage_ms[name] = round(dt, 2)
            extra = " ".join(f"{kk}={vv}" for kk, vv in list(mm.items())[:4] if not isinstance(vv, (dict, list)))
            log.info(f"run={run_id} slot={slot_label} stage={name} ms={dt:.1f} {extra}")
            jsonl_append({"ts": datetime.now(timezone.utc).isoformat(), "run": str(run_id),
                          "slot": slot_label, "stage": name, "ms": round(dt, 2), "metrics": mm}, log_path)
            return out, mm
        except Exception as e:
            dt = _now_ms(t0)
            stage_ms[name] = round(dt, 2)
            raise

    try:
        emit("start", f"Processing {tag} ({slot_group or 'no group'}) · "
                      f"{len(data) / 1024:.0f}KB {original_filename or 'upload'}")
        img, lm = timed("load", stage_load, data)
        m.update(lm)
        emit("load", f"Loaded {lm.get('orig_w')}×{lm.get('orig_h')} {lm.get('format') or '?'} "
                     f"({lm.get('megapixels')}MP) · EXIF orientation {lm.get('exif_orientation')} "
                     f"→ transposed upright", stage_ms["load"])
        dinfo, dm = timed("detect", stage_detect, img)
        m.update({"area_ratio": dm["area_ratio"], "detected_angle": dm["angle"],
                  "second_ratio": dm["second_ratio"], "quad_confidence": dm["quad_confidence"]})
        bg = "dark background" if dm.get("bg_dark") else "light background"
        emit("detect", f"Detecting page on {bg} · covers {dm['area_ratio'] * 100:.1f}% of the photo "
                       f"(warp needs 30–98%, else trim only) · angle {dm['angle']}° · "
                       f"quad confidence {dm['quad_confidence']} · 2nd contour {dm['second_ratio']}",
             stage_ms["detect"])
        cropped, cm = timed("crop_deskew", stage_crop_deskew, img, dinfo, dm)
        m["fallback"] = cm.get("fallback", "")
        m["warp"] = cm.get("warp", "")
        if cm.get("warp") == "poly_warp":
            emit("crop_deskew", f"Cropping page: 4-corner perspective warp + 2% padding · "
                         f"background removed, only the document kept", stage_ms["crop_deskew"])
        elif cm.get("warp") == "rect_warp":
            emit("crop_deskew", f"Cropping page: bounding-box warp + 2% padding · "
                         f"background removed, only the document kept", stage_ms["crop_deskew"])
        else:
            emit("crop_deskew", f"No clean page edge (area {dm['area_ratio'] * 100:.1f}% outside 30–98%) → "
                         f"{m['fallback']}: trimming white margins instead of warping",
                 stage_ms["crop_deskew"])

        # OCR callback wiring
        def ocr_fn(im):
            t0 = time.perf_counter(); c0 = time.process_time()
            reason = "ALWAYS" if ocr_mode == "ALWAYS" else "FALLBACK"
            detail = {"success": True, "exception": ""}
            try:
                info = osd_info(im, ocr_languages)
                evt = {"reason": reason, "raw_osd": info["raw"], "rotate": info["rotate"],
                       "confidence": info["confidence"], "script": info["script"],
                       "script_conf": info["script_conf"], "input_size": list(im.size),
                       "thumb_size": list(info.get("thumb", im.size)),
                       "tesseract_version": tesseract_version(), "languages": ocr_languages,
                       "config": "--psm 0", "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                       "cpu_ms": round((time.process_time() - c0) * 1000, 2)}
                if ocr_mode == "ALWAYS" and collect_ocr_detail:
                    full = full_ocr(im, ocr_languages)
                    evt.update({"full_text": full["text"], "words": full["words"][:500],
                                "mean_conf": full["mean_conf"], "min_conf": full["min_conf"],
                                "word_count": full["word_count"]})
                else:
                    evt.update({"full_text": "", "words": [], "mean_conf": -1, "min_conf": -1, "word_count": 0})
                ocr_events.append(evt)
                return {"rotate": info["rotate"], "confidence": info["confidence"]}
            except Exception as e:
                ocr_events.append({"reason": reason, "success": False, "exception": str(e)[:500],
                                   "tesseract_version": tesseract_version(), "languages": ocr_languages,
                                   "config": "--psm 0", "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
                                   "cpu_ms": round((time.process_time() - c0) * 1000, 2),
                                   "raw_osd": "", "rotate": 0, "confidence": -1, "script": "",
                                   "script_conf": -1, "input_size": list(im.size), "full_text": "",
                                   "words": [], "mean_conf": -1, "min_conf": -1, "word_count": 0})
                raise
        ocr_callable = ocr_fn if (tesseract_available() and (
            ocr_mode == "ALWAYS" or (ocr_mode == "FALLBACK" and ocr_fallback_enabled))) else None
        oriented, om = timed("orientation", stage_orientation, cropped,
                             expected_orientation, ocr_mode, ocr_callable,
                             ocr_fallback_enabled)
        m.update({"orientation_method": om["method"], "rotation": om["rotation"],
                  "osd_confidence": om.get("osd_confidence")})
        if om.get("osd_confidence") is not None and om["method"].startswith("osd"):
            emit("orientation", f"OCR text read (confidence {om['osd_confidence']}) → "
                                f"rotated {om['rotation']}° (method {om['method']}, mode {ocr_mode})",
                 stage_ms["orientation"])
        elif om["rotation"]:
            emit("orientation", f"Aspect expects {expected_orientation} → "
                                f"rotated {om['rotation']}° (method {om['method']}, no OCR needed)",
                 stage_ms["orientation"])
        else:
            emit("orientation", f"Already upright (method {om['method']}, mode {ocr_mode}) · no rotation",
                 stage_ms["orientation"])
        aligned, am = timed("align", stage_align, oriented)
        m.update({"skew_angle": am["skew_angle"], "deskew_deg": am["corrected_deg"],
                  "aligned": am["aligned"], "align_method": am["method"],
                  "repad": am.get("repad", False),
                  "residual_skew": am.get("residual_skew", 0.0)})
        if am["aligned"]:
            emit("align", f"Original tilt {am['skew_angle']}° (≥ 0.3° threshold, limit 15°, "
                          f"{am['n_lines']} text lines via {am['method']}) → rotated "
                          f"{am['corrected_deg']}° to fix, border re-squared + uniform 2% padding · "
                          f"processed tilt {am.get('residual_skew', 0.0)}°", stage_ms["align"])
        else:
            emit("align", f"Level · original tilt {am['skew_angle']}°, processed tilt "
                          f"{am.get('residual_skew', am['skew_angle'])}° "
                          f"(below 0.3° threshold) · skipping ({am['method']})",
                 stage_ms["align"])
        enhanced, em = timed("enhance", stage_enhance, aligned, enhance_enabled)
        if enhance_enabled:
            emit("enhance", "Contrast boost (CLAHE, no binarisation — stamps kept)",
                 stage_ms["enhance"])
        else:
            emit("enhance", "Contrast boost OFF by settings · skipping", stage_ms["enhance"])
        jpeg, jm = timed("compose", stage_compose, enhanced)
        m.update({"out_w": jm["out_w"], "out_h": jm["out_h"], "jpeg_bytes": jm["jpeg_bytes"]})
        emit("compose", f"Print image {jm['out_w']}×{jm['out_h']} JPEG {jm['jpeg_bytes'] / 1024:.0f}KB "
                        f"ready for the A4 page", stage_ms["compose"])
        if m.get("fallback") not in ("", "no_crop"):
            warnings.append(f"fallback:{m['fallback']}")
            status = "WARN"
        if m.get("rotation", 0):
            warnings.append(f"rotated:{m['rotation']}")
            if status == "OK": status = "WARN"
        if m.get("aligned"):
            warnings.append(f"deskew:{m.get('deskew_deg', 0)}deg")
            if status == "OK": status = "WARN"
        total = _now_ms(t_all)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        emit("__done__", f"Done {tag} in {total:.0f}ms · {status} "
                         f"{('[' + ', '.join(warnings) + ']') if warnings else '[clean]'}", total)
        m.update({"total_ms": round(total, 2), "rss_delta": _rss() - rss0, "alloc_peak": peak,
                  "thread": str(threading.get_ident())[-6:], "status": status,
                  "stage_ms": stage_ms, "warnings": warnings, "exception": "",
                  "steps": steps,
                  "cpu_ms": round((time.process_time() - cpu0) * 1000, 2),
                  "source_format": lm.get("format", ""), "orig_w": lm.get("orig_w", 0),
                  "orig_h": lm.get("orig_h", 0), "exif_orientation": lm.get("exif_orientation", 1),
                  "megapixels": lm.get("megapixels", 0)})
        jsonl_append({"ts": datetime.now(timezone.utc).isoformat(), "run": str(run_id),
                      "slot": slot_label, "stage": "__image_done__", "ms": round(total, 2),
                      "status": status, "metrics": m}, log_path)
        log.info(f"run={run_id} slot={slot_label} stage=__done__ ms={total:.1f} status={status}")
        return jpeg, m, ocr_events
    except Exception as e:
        total = _now_ms(t_all)
        try: tracemalloc.stop()
        except Exception: pass
        emit("__failed__", f"FAILED {tag} after {total:.0f}ms: {str(e)[:200]}", total)
        m.update({"total_ms": round(total, 2), "rss_delta": _rss() - rss0, "alloc_peak": 0,
                  "thread": str(threading.get_ident())[-6:], "status": "FAILED",
                  "stage_ms": stage_ms, "warnings": warnings, "exception": str(e)[:1000],
                  "steps": steps,
                  "cpu_ms": round((time.process_time() - cpu0) * 1000, 2)})
        jsonl_append({"ts": datetime.now(timezone.utc).isoformat(), "run": str(run_id),
                      "slot": slot_label, "stage": "__image_failed__", "ms": round(total, 2),
                      "exception": str(e)[:500]}, log_path)
        raise
