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

    log_cb(stage, line): optional callback receiving a human-readable log line
    for every step (also collected in metrics["steps"]). The caller (e.g. the
    web UI) uses it for a live "processing CITI FRONT — align: tilt 6.9° found,
    deskewing…" feed. Never breaks the pipeline if it raises.
    """
    tracemalloc.start()
    rss0 = _rss()
    stage_ms, m, warnings, ocr_events, steps = {}, {}, [], [], []
    status, exc = "OK", ""
    t_all = time.perf_counter()
    cpu0 = time.process_time()
    tag = slot_label or f"#{slot_order}"

    def say(stage, msg):
        line = f"[{tag}] {msg}"
        steps.append(line)
        try:
            if log_cb is not None:
                log_cb(stage, line)
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
        say("start", f"processing {tag} ({slot_group or 'no group'}) — "
                      f"{len(data) / 1024:.0f}KB {original_filename or 'upload'}")
        img, lm = timed("load", stage_load, data)
        m.update(lm)
        say("load", f"load: {lm.get('orig_w')}×{lm.get('orig_h')} {lm.get('format') or '?'} "
                     f"({lm.get('megapixels')}MP), EXIF orientation {lm.get('exif_orientation')} "
                     f"→ transposed upright")
        dinfo, dm = timed("detect", stage_detect, img)
        m.update({"area_ratio": dm["area_ratio"], "detected_angle": dm["angle"],
                  "second_ratio": dm["second_ratio"], "quad_confidence": dm["quad_confidence"]})
        say("detect", f"detect: page covers {dm['area_ratio'] * 100:.1f}% of the photo "
                       f"(needs 30–98% for a perspective warp), contour angle {dm['angle']}°, "
                       f"quad confidence {dm['quad_confidence']}")
        cropped, cm = timed("crop_deskew", stage_crop_deskew, img, dinfo, dm)
        m["fallback"] = cm.get("fallback", "")
        if not m["fallback"]:
            say("crop", "crop: clean page quad found → perspective warp applied "
                         "(keystone fixed, page straightened)")
        else:
            say("crop", f"crop: no clean page quad (area {dm['area_ratio'] * 100:.1f}% outside 30–98%) → "
                         f"{m['fallback']} fallback: trimming white margins instead of warping")

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
            say("orientation", f"OCR OSD read the text (confidence {om['osd_confidence']}) → "
                               f"rotated {om['rotation']}° (method {om['method']}, mode {ocr_mode})")
        elif om["rotation"]:
            say("orientation", f"aspect expects {expected_orientation} → "
                               f"rotated {om['rotation']}° (method {om['method']}, no OCR needed)")
        else:
            say("orientation", f"already upright (method {om['method']}, mode {ocr_mode}) — "
                               f"no rotation")
        aligned, am = timed("align", stage_align, oriented)
        m.update({"skew_angle": am["skew_angle"], "deskew_deg": am["corrected_deg"],
                  "aligned": am["aligned"], "align_method": am["method"],
                  "repad": am.get("repad", False)})
        if am["aligned"]:
            say("align", f"align: tilt {am['skew_angle']}° found (≥ 0.3° threshold, limit 15°, "
                         f"{am['n_lines']} text lines via {am['method']}) → rotating "
                         f"{am['corrected_deg']}° to fix, then re-squaring border + uniform "
                         f"2% padding so nothing stays rotated")
        else:
            say("align", f"align: level (tilt {am['skew_angle']}°, below 0.3° threshold) — "
                         f"skipping ({am['method']})")
        enhanced, em = timed("enhance", stage_enhance, aligned, enhance_enabled)
        if enhance_enabled:
            say("enhance", "enhance: contrast boost (CLAHE) applied")
        else:
            say("enhance", "enhance: contrast boost OFF by settings — skipping")
        jpeg, jm = timed("compose", stage_compose, enhanced)
        m.update({"out_w": jm["out_w"], "out_h": jm["out_h"], "jpeg_bytes": jm["jpeg_bytes"]})
        say("compose", f"compose: print image {jm['out_w']}×{jm['out_h']} JPEG {jm['jpeg_bytes'] / 1024:.0f}KB "
                        f"ready for the A4 page")
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
        say("__done__", f"done {tag} in {total:.0f}ms — {status} "
                         f"{('[' + ', '.join(warnings) + ']') if warnings else '[clean]'}")
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
        say("__failed__", f"FAILED {tag} after {total:.0f}ms: {str(e)[:200]}")
        m.update({"total_ms": round(total, 2), "rss_delta": _rss() - rss0, "alloc_peak": 0,
                  "thread": str(threading.get_ident())[-6:], "status": "FAILED",
                  "stage_ms": stage_ms, "warnings": warnings, "exception": str(e)[:1000],
                  "steps": steps,
                  "cpu_ms": round((time.process_time() - cpu0) * 1000, 2)})
        jsonl_append({"ts": datetime.now(timezone.utc).isoformat(), "run": str(run_id),
                      "slot": slot_label, "stage": "__image_failed__", "ms": round(total, 2),
                      "exception": str(e)[:500]}, log_path)
        raise
