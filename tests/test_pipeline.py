import io, json, os
os.environ.setdefault("GEN_BACKEND", "sync")
import django
from django.test import Client
from pipeline.synth import generate
from pipeline.runner import process_image
from pipeline.detect import stage_detect
from pipeline.load import stage_load
from PIL import Image

def _img(scenario="clean_scan", seed=1):
    data, ext = generate(scenario, seed=seed)
    return data

def test_detect_clean_scan():
    data = _img("clean_scan")
    img, _ = stage_load(data)
    info, m = stage_detect(img)
    assert m["area_ratio"] > 0.2, m

def test_white_on_white_fallback():
    from pipeline.crop import stage_crop_deskew
    data = _img("white_on_white")
    jpeg, m, _ = process_image(data, slot_label="W", ocr_mode="OFF")
    assert m.get("fallback") in ("whitespace_trim", "no_crop", ""), m
    assert len(jpeg) > 1000

def test_rotation_sideways_aspect():
    data = _img("sideways_90")
    img, _ = stage_load(data)
    from pipeline.orient import stage_orientation
    out, m = stage_orientation(img, expected="portrait", ocr_mode="OFF", ocr_fn=None)
    assert m["rotation"] == 90
    assert out.size[1] >= out.size[0]

def test_ocr_modes():
    from pipeline.ocr import tesseract_available
    import pytest
    data = _img("clean_scan")
    jpeg, m, evts = process_image(data, slot_label="O", ocr_mode="OFF")
    assert evts == []
    if not tesseract_available():
        pytest.skip("tesseract missing")
    jpeg, m, evts = process_image(data, slot_label="O2", ocr_mode="ALWAYS")
    assert len(evts) == 1 and "raw_osd" in evts[0]

def test_fallback_toggle_gates_osd_without_tesseract():
    """OCR_FALLBACK_ENABLED=false must prevent any OCR call, even with a stub ocr_fn."""
    from pipeline.orient import stage_orientation
    data = _img("clean_scan")
    img, _ = stage_load(data)
    calls = []
    def fake_ocr(im):
        calls.append(im.size)
        return {"rotate": 90, "confidence": 5.0}
    import pipeline.orient as orient_mod
    real_avail = orient_mod._tesseract_available
    orient_mod._tesseract_available = lambda: True  # pretend tesseract exists
    try:
        # ambiguous (expected=any) + FALLBACK + toggle ON -> OCR fires
        out, m = stage_orientation(img, expected="any", ocr_mode="FALLBACK",
                                   ocr_fn=fake_ocr, ocr_fallback_enabled=True)
        assert len(calls) == 1 and m["method"] == "osd_fallback" and m["rotation"] == 90
        # toggle OFF -> no OCR call, heuristic only, gated flag recorded
        out2, m2 = stage_orientation(img, expected="any", ocr_mode="FALLBACK",
                                     ocr_fn=fake_ocr, ocr_fallback_enabled=False)
        assert len(calls) == 1 and m2["fallback_gated"] is True
        assert m2["rotation"] == 0 and m2["method"] == "exif"
        # ALWAYS bypasses the toggle
        out3, m3 = stage_orientation(img, expected="any", ocr_mode="ALWAYS",
                                     ocr_fn=fake_ocr, ocr_fallback_enabled=False)
        assert len(calls) == 2 and m3["method"] == "osd"
    finally:
        orient_mod._tesseract_available = real_avail

def test_fallback_toggle_default_and_snapshot(settings):
    assert settings.OCR_FALLBACK_ENABLED is True
    from core.services import _settings_snapshot
    snap = _settings_snapshot()
    assert snap["OCR_FALLBACK_ENABLED"] is True

def test_align_straight_noop():
    from pipeline.align import stage_align
    data = _img("clean_scan")
    img, _ = stage_load(data)
    out, m = stage_align(img)
    assert m["aligned"] is False, m
    assert abs(m["skew_angle"]) < 0.3, m
    assert out.size == img.size

def test_align_corrects_tilt():
    from pipeline.align import stage_align
    data = _img("clean_scan")
    img, _ = stage_load(data)
    tilted = img.rotate(-7, expand=True, fillcolor=(255, 255, 255))
    out, m = stage_align(tilted)
    assert m["aligned"] is True, m
    assert abs(abs(m["skew_angle"]) - 7) < 1.0, m
    # processed (residual) tilt must be smaller than the original tilt
    assert abs(m["residual_skew"]) < abs(m["skew_angle"]), m
    assert abs(m["residual_skew"]) < 1.0, m
    # residual after correction should be ~level
    _, m2 = stage_align(out)
    assert m2["aligned"] is False, m2

def test_pipeline_includes_align_stage():
    from pipeline.runner import STAGES
    assert "align" in STAGES
    data = _img("clean_scan")
    jpeg, m, _ = process_image(data, slot_label="A", ocr_mode="OFF")
    assert "align" in m["stage_ms"], m["stage_ms"]
    assert "skew_angle" in m and "deskew_deg" in m and "align_method" in m

def test_pipeline_align_fixes_tilted_photo():
    data = _img("clean_scan")
    img, _ = stage_load(data)
    tilted = img.rotate(-7, expand=True, fillcolor=(255, 255, 255))
    buf = io.BytesIO(); tilted.save(buf, "JPEG", quality=85)
    jpeg, m, _ = process_image(buf.getvalue(), slot_label="T", ocr_mode="OFF")
    assert m["aligned"] is True, m
    assert abs(abs(m["deskew_deg"]) - 7) < 1.5, m
    assert any(str(w).startswith("deskew:") for w in m["warnings"])
    assert len(jpeg) > 1000

def test_align_repad_leaves_uniform_border():
    """After deskew the border/padding must be axis-aligned (equal on all sides)."""
    import numpy as np
    from pipeline.align import stage_align
    data = _img("clean_scan")
    img, _ = stage_load(data)
    tilted = img.rotate(-7, expand=True, fillcolor=(255, 255, 255))
    out, m = stage_align(tilted)
    assert m["aligned"] is True and m["repad"] is True, m
    g = np.array(out.convert("L"))
    mask = g < 240
    ys, xs = np.where(mask)
    w, h = out.size
    margins = {int(xs.min()), int(w - 1 - xs.max()),
               int(ys.min()), int(h - 1 - ys.max())}
    assert len(margins) == 1, margins  # uniform padding, nothing rotated

def test_process_image_logs_steps_with_thresholds():
    entries = []
    data = _img("photo_dark_bg", seed=3)
    jpeg, m, _ = process_image(data, slot_label="CITI FRONT", slot_group="Docs",
                               ocr_mode="OFF",
                               log_cb=lambda stage, entry: entries.append(entry))
    assert len(jpeg) > 1000
    # log_cb got one structured entry per step, steps persisted in metrics
    assert len(entries) >= 7, entries
    assert len(m["steps"]) == len(entries)
    assert all({"slot", "stage", "msg", "ms"} <= set(e) for e in entries)
    assert all(e["slot"] == "CITI FRONT" for e in entries)
    stages = [e["stage"] for e in entries]
    for s in ("load", "detect", "crop_deskew", "orientation", "align", "enhance", "compose"):
        assert s in stages, stages
    timed = [e for e in entries if e["stage"] not in ("start",)]
    assert all(isinstance(e["ms"], (int, float)) and e["ms"] >= 0 for e in timed)
    blob = "\n".join(e["msg"] for e in entries)
    for needle in ("30–98%", "0.3°"):  # thresholds are stated
        assert needle in blob, blob
    assert "dark background" in blob  # bg type stated for bg photos
    # a tilted full-bleed shot exercises the deskew branch incl. its 15° limit
    tdata = _img("clean_scan")
    timg, _ = stage_load(tdata)
    tilted = timg.rotate(-7, expand=True, fillcolor=(255, 255, 255))
    buf = io.BytesIO(); tilted.save(buf, "JPEG", quality=85)
    _, tm, _ = process_image(buf.getvalue(), slot_label="TILT", ocr_mode="OFF")
    tblob = "\n".join(e["msg"] for e in tm["steps"])
    assert "15°" in tblob and tm["aligned"] is True, tblob
    # align step states original vs processed tilt
    align_msgs = [e["msg"] for e in tm["steps"] if e["stage"] == "align"]
    assert align_msgs and "Original tilt" in align_msgs[0]
    assert "processed tilt" in align_msgs[0], align_msgs
    assert "residual_skew" in tm, tm

def test_detect_extracts_document_from_dark_bg():
    """Spec 5.2: photo of a page on a dark surface -> quad in 30-98%, warp path."""
    from pipeline.crop import stage_crop_deskew
    for sc in ("photo_dark_bg", "photo_wood_like_bg"):
        data = _img(sc, seed=3)
        img, _ = stage_load(data)
        info, dm = stage_detect(img)
        assert 0.30 <= dm["area_ratio"] <= 0.98, (sc, dm)
        assert dm["second_ratio"] <= 0.40, (sc, dm)
        assert dm["bg_dark"] is True, (sc, dm)
        out, cm = stage_crop_deskew(img, info, dm)
        assert cm["warp"] in ("poly_warp", "rect_warp") and cm["fallback"] == "", (sc, cm)
        # only the document kept: photo 1700x2100 -> doc-sized portrait output
        assert out.size[1] > out.size[0] and out.size[0] < 1700, (sc, out.size)

def test_detect_white_on_white_trims_only():
    data = _img("white_on_white")
    img, _ = stage_load(data)
    info, dm = stage_detect(img)
    assert dm["area_ratio"] > 0.98, dm  # edge not found -> trim only
    assert dm["bg_dark"] is False, dm

def test_upload_rejects_two_objects():
    from core.validation import validate_upload
    data, _ = generate("two_objects", seed=1)
    ok, err, measured, logs = validate_upload(data, "two.jpg")
    assert not ok and "page sanity" in err, (ok, err)
    assert measured.get("second_ratio", 0) > 0.40, measured
