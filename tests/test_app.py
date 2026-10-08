import os
os.environ["GEN_BACKEND"] = "sync"
import json
import pytest
from django.test import Client
from core.models import Session, Slot, GenerationRun, ImageRun, OcrLog, UploadLog
from core.presets import make_slots
from pipeline.synth import generate

@pytest.mark.django_db
def test_upload_validation_each_check(settings):
    from core.validation import validate_upload
    data, _ = generate("clean_scan", seed=1)
    ok, err, measured, logs = validate_upload(data, "doc.jpg")
    assert ok, err
    assert {l["check"] for l in logs} >= {"extension", "mime", "size", "decode", "pixels", "min_resolution", "blur", "page_detect"}
    # bad ext
    ok, err, _, _ = validate_upload(data, "doc.pdf")
    assert not ok and "extension" in err
    # corrupt
    ok, err, _, _ = validate_upload(b"garbage-bytes", "x.jpg")
    assert not ok
    # low res
    d2, _ = generate("low_res", seed=1)
    ok, err, _, _ = validate_upload(d2, "x.jpg")
    assert not ok and ("resolution" in err or "blur" in err or "page" in err)
    # blurry
    d3, _ = generate("blurry", seed=1)
    ok, err, _, _ = validate_upload(d3, "x.jpg")
    assert not ok and "blurr" in err

@pytest.mark.django_db
def test_generation_sync_pair_pages_stale_doubleclick(settings):
    settings.GEN_BACKEND = "sync"
    s = Session.objects.create(name="T", ocr_mode="OFF")
    make_slots(s, "realistic")
    c = Client()
    # fill all required slots with clean scans via API
    for slot in s.slots.order_by("order"):
        data, _ = generate("clean_scan", seed=slot.order + 1, title=slot.label)
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = c.post(f"/api/slots/{slot.id}/upload/", {"file": SimpleUploadedFile(f"{slot.label}.jpg", data, "image/jpeg")})
        assert r.status_code == 200, r.content[:300]
    r = c.post(f"/api/sessions/{s.id}/generate/")
    assert r.status_code == 202, r.content[:300]
    s.refresh_from_db()
    assert s.final_status == "READY", s.final_error
    assert s.final_pages >= 1
    # pair layout: citizenship front+back share one page -> realistic 12 slots, one pair => 11 pages
    assert s.final_pages == 11, s.final_pages
    assert ImageRun.objects.filter(generation_run__session=s).count() == 12
    assert all("load" in (x.stage_ms or {}) for x in ImageRun.objects.filter(generation_run__session=s))
    assert OcrLog.objects.filter(session=s).count() == 0
    # stale after replace
    slot = s.slots.first()
    data, _ = generate("clean_scan", seed=99, title=slot.label)
    from django.core.files.uploadedfile import SimpleUploadedFile
    c.post(f"/api/slots/{slot.id}/upload/", {"file": SimpleUploadedFile("n.jpg", data, "image/jpeg")})
    s.refresh_from_db()
    assert s.is_stale is True
    # second generate clears stale
    old_pdf = s.final_media_path
    c.post(f"/api/sessions/{s.id}/generate/")
    s.refresh_from_db()
    assert s.is_stale is False and s.final_status == "READY"
    # double-click guard: force PROCESSING then 409
    s.final_status = "PROCESSING"; s.save()
    r = c.post(f"/api/sessions/{s.id}/generate/")
    assert r.status_code == 409
    s.final_status = "READY"; s.save()

@pytest.mark.django_db
def test_dynamic_slots_api():
    c = Client()
    r = c.post("/api/sessions/", data='{"name":"D"}', content_type="application/json")
    sid = r.json()["data"]["id"]
    c.post(f"/api/sessions/{sid}/slots/", data='{"bulk":{"count":25,"prefix":"L","group":"Load"}}', content_type="application/json")
    d = c.get(f"/api/sessions/{sid}/").json()["data"]
    assert len(d["slots"]) == 25
    first = d["slots"][0]["id"]
    c.post(f"/api/slots/{first}/", data='{"action":"move","delta":1}', content_type="application/json")
    d2 = c.get(f"/api/sessions/{sid}/").json()["data"]
    assert d2["slots"][1]["id"] == first
    c.delete(f"/api/slots/{first}/")
    assert len(c.get(f"/api/sessions/{sid}/").json()["data"]["slots"]) == 24

@pytest.mark.django_db
def test_logging_jsonl_and_ocr_rows(settings):
    settings.GEN_BACKEND = "sync"
    from django.conf import settings as dj
    s = Session.objects.create(name="L", ocr_mode="ALWAYS")
    from core.presets import make_slots
    make_slots(s, "25", count=2) if False else None
    from core.models import Slot
    for i in range(2):
        Slot.objects.create(session=s, label=f"D{i}", group="G", order=i, layout="single", required=True)
    c = Client()
    from django.core.files.uploadedfile import SimpleUploadedFile
    for slot in s.slots.all():
        data, _ = generate("clean_scan", seed=slot.order + 5, title=slot.label)
        c.post(f"/api/slots/{slot.id}/upload/", {"file": SimpleUploadedFile("a.jpg", data, "image/jpeg")})
    c.post(f"/api/sessions/{s.id}/generate/")
    from pipeline.ocr import tesseract_available
    if tesseract_available():
        assert OcrLog.objects.filter(session=s).count() == 2
        assert all(o.raw_osd for o in OcrLog.objects.filter(session=s))
    assert os.path.exists("logs/pipeline.jsonl")
    with open("logs/pipeline.jsonl") as f:
        line = f.readline()
        json.loads(line)

@pytest.mark.django_db
def test_run_timing_split(settings):
    """total = images + pdf (+ overhead); per-image times recorded."""
    settings.GEN_BACKEND = "sync"
    s = Session.objects.create(name="T", ocr_mode="OFF")
    from core.models import Slot
    for i in range(3):
        Slot.objects.create(session=s, label=f"D{i}", group="G", order=i, layout="single", required=True)
    c = Client()
    from django.core.files.uploadedfile import SimpleUploadedFile
    for slot in s.slots.all():
        data, _ = generate("clean_scan", seed=slot.order + 5, title=slot.label)
        c.post(f"/api/slots/{slot.id}/upload/", {"file": SimpleUploadedFile("a.jpg", data, "image/jpeg")})
    c.post(f"/api/sessions/{s.id}/generate/")
    g = GenerationRun.objects.filter(session=s).latest("id")
    assert g.final_status == "READY"
    assert g.images_ms > 0 and g.pdf_ms > 0
    assert g.pdf_ms <= g.total_ms  # pdf build is serial; images run in parallel so their sum may exceed wall total
    assert g.images_ms >= g.total_ms - g.pdf_ms - g.queue_wait_ms - 2000  # summed work covers the parallel window
    assert abs(g.images_ms - sum(x.total_ms for x in g.images.all())) < g.images_ms * 0.2 + 50
    rows = c.get(f"/api/runs/{g.id}/").json()["data"]
    assert rows["pdf_ms"] == g.pdf_ms and rows["images_ms"] == g.images_ms
    per = c.get(f"/api/runs/{g.id}/images/").json()["data"]
    assert len(per) == 3 and all(x["total_ms"] > 0 for x in per)

@pytest.mark.django_db
def test_fallback_toggle_reaches_pipeline(settings, monkeypatch):
    """GenerationRun snapshot carries OCR_FALLBACK_ENABLED into process_image."""
    import core.services as svc
    settings.GEN_BACKEND = "sync"
    settings.OCR_FALLBACK_ENABLED = False
    s = Session.objects.create(name="FB", ocr_mode="FALLBACK")
    from core.models import Slot
    Slot.objects.create(session=s, label="D0", group="G", order=0, layout="single", required=True)
    c = Client()
    from django.core.files.uploadedfile import SimpleUploadedFile
    slot = s.slots.first()
    data, _ = generate("clean_scan", seed=3, title=slot.label)
    c.post(f"/api/slots/{slot.id}/upload/", {"file": SimpleUploadedFile("a.jpg", data, "image/jpeg")})
    seen = {}
    real = svc.process_image
    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)
    monkeypatch.setattr(svc, "process_image", spy)
    c.post(f"/api/sessions/{s.id}/generate/")
    assert seen.get("ocr_fallback_enabled") is False
    assert OcrLog.objects.filter(session=s).count() == 0

def _fill2(c, s, seed=5):
    from django.core.files.uploadedfile import SimpleUploadedFile
    for slot in s.slots.all():
        data, _ = generate("clean_scan", seed=seed + slot.order, title=slot.label)
        r = c.post(f"/api/slots/{slot.id}/upload/",
                   {"file": SimpleUploadedFile("a.jpg", data, "image/jpeg")})
        assert r.status_code == 200

@pytest.mark.django_db
def test_process_then_create_pdf(settings):
    """Process Images -> Create PDF (fast, no reprocessing) -> stale after change."""
    settings.GEN_BACKEND = "sync"
    s = Session.objects.create(name="PC", ocr_mode="OFF")
    Slot.objects.create(session=s, label="D0", group="G", order=0, layout="single", required=True)
    Slot.objects.create(session=s, label="D1", group="G", order=1, layout="single", required=True)
    c = Client()
    _fill2(c, s)
    # 1. process only: timings, no PDF
    r = c.post(f"/api/sessions/{s.id}/generate/", data='{"process_only": true}',
               content_type="application/json")
    assert r.status_code == 202
    s.refresh_from_db()
    assert s.final_status == "READY" and not s.final_media_path
    # 2. create PDF from cache: fast, images_ms == 0
    r = c.post(f"/api/sessions/{s.id}/create-pdf/")
    assert r.status_code == 202, r.content[:200]
    s.refresh_from_db()
    assert s.final_status == "READY" and s.final_media_path
    g = GenerationRun.objects.filter(session=s).latest("id")
    assert g.pdf_ms > 0 and g.images_ms == 0 and g.pages == 2
    # 3. change a slot -> cache invalid -> 422
    from django.core.files.uploadedfile import SimpleUploadedFile
    slot = s.slots.first()
    data, _ = generate("clean_scan", seed=99, title=slot.label)
    c.post(f"/api/slots/{slot.id}/upload/", {"file": SimpleUploadedFile("n.jpg", data, "image/jpeg")})
    r = c.post(f"/api/sessions/{s.id}/create-pdf/")
    assert r.status_code == 422

@pytest.mark.django_db
def test_create_pdf_without_process(settings):
    settings.GEN_BACKEND = "sync"
    s = Session.objects.create(name="NP", ocr_mode="OFF")
    c = Client()
    r = c.post(f"/api/sessions/{s.id}/create-pdf/")
    assert r.status_code == 422

@pytest.mark.django_db
def test_preview_synthetic():
    c = Client()
    r = c.get("/api/preview-synthetic/", {"scenario": "clean_scan", "seed": "1"})
    assert r.status_code == 200, r.content[:200]
    assert r["Content-Type"] == "image/jpeg" and len(r.content) > 1000
    assert r["X-Scenario"] == "clean_scan"
    r2 = c.get("/api/preview-synthetic/", {"scenario": "__mix__", "seed": "2", "mix": "photo_dark_bg=100"})
    assert r2.status_code == 200 and r2["X-Scenario"] == "photo_dark_bg"
    assert len(r2.content) > 1000
    assert c.get("/api/preview-synthetic/", {"scenario": "nope"}).status_code == 400
    assert c.get("/api/preview-synthetic/", {"scenario": "corrupt_file"}).status_code == 422
