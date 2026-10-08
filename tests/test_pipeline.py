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
    lines = []
    data = _img("photo_dark_bg", seed=3)
    jpeg, m, _ = process_image(data, slot_label="CITI FRONT", slot_group="Docs",
                               ocr_mode="OFF",
                               log_cb=lambda stage, line: lines.append((stage, line)))
    assert len(jpeg) > 1000
    # log_cb got one line per step, steps persisted in metrics
    assert len(lines) >= 7, lines
    assert len(m["steps"]) == len(lines)
    blob = "\n".join(m["steps"])
    assert "CITI FRONT" in blob
    for needle in ("30–98%", "0.3°", "15°"):  # thresholds are stated
        assert needle in blob, blob
    assert "tilt" in blob and "crop" in blob.lower()
