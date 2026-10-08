"""Upload-time validation. Returns (ok, error, measured_dict, logs)."""
import io, time
from PIL import Image
from django.conf import settings

def _sniff_mime(data: bytes, ext: str):
    try:
        import magic
        mime = magic.from_buffer(data[:4096], mime=True)
        return mime
    except Exception:
        pass
    try:
        import filetype
        kind = filetype.guess(data)
        if kind: return kind.mime
    except Exception:
        pass
    # Pillow fallback
    try:
        img = Image.open(io.BytesIO(data)); fmt = (img.format or "").lower()
        return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "webp": "image/webp"}.get(fmt, "application/octet-stream")
    except Exception:
        return "application/octet-stream"

def _blur_score(pil_img):
    import cv2, numpy as np
    g = np.array(pil_img.convert("L"))
    h, w = g.shape
    s = min(1.0, 800 / max(h, w))
    if s < 1:
        g = cv2.resize(g, (int(w * s), int(h * s)))
    return float(cv2.Laplacian(g, cv2.CV_64F).var())

MIME_OK = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
           ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heic"}

def validate_upload(data: bytes, filename: str):
    logs = []
    def log(check, value, threshold, passed, dt):
        logs.append({"check": check, "value": str(value), "threshold": str(threshold),
                     "passed": passed, "duration_ms": round(dt * 1000, 2)})
    ext = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
    # 1 extension
    t0 = time.perf_counter()
    allowed = {".jpg", ".jpeg", ".png"}
    if settings.UPLOAD_ALLOW_WEBP: allowed.add(".webp")
    if settings.UPLOAD_ALLOW_HEIC: allowed.add(".heic"); allowed.add(".heif")
    ok = ext in allowed
    log("extension", ext, sorted(allowed), ok, time.perf_counter() - t0)
    if not ok: return False, f"extension {ext} not allowed", {}, logs
    # 2 MIME
    t0 = time.perf_counter()
    mime = _sniff_mime(data, ext)
    ok = (MIME_OK.get(ext, "") == mime) or (ext in (".jpg", ".jpeg") and mime == "image/jpeg")
    log("mime", mime, MIME_OK.get(ext), ok, time.perf_counter() - t0)
    if not ok: return False, f"MIME {mime} does not match {ext}", {"mime": mime}, logs
    # 3 size
    t0 = time.perf_counter()
    maxb = settings.UPLOAD_MAX_MB * 1024 * 1024
    ok = len(data) <= maxb
    log("size", f"{len(data)/1e6:.2f}MB", f"<={settings.UPLOAD_MAX_MB}MB", ok, time.perf_counter() - t0)
    if not ok: return False, f"file too large ({len(data)/1e6:.1f}MB > {settings.UPLOAD_MAX_MB}MB)", {"bytes": len(data)}, logs
    # 4 decode
    t0 = time.perf_counter()
    try:
        im = Image.open(io.BytesIO(data)); im.verify()
        im = Image.open(io.BytesIO(data)); im.load()
    except Exception as e:
        log("decode", f"corrupt: {e}"[:120], "decodable", False, time.perf_counter() - t0)
        return False, f"corrupt/undecodable image: {e}"[:200], {}, logs
    log("decode", f"{im.format} {im.size}", "decodable", True, time.perf_counter() - t0)
    w, h = im.size
    mp = w * h / 1e6
    # 5 pixel limit
    t0 = time.perf_counter()
    ok = mp <= settings.UPLOAD_MAX_MEGAPIXELS
    log("pixels", f"{mp:.1f}MP", f"<={settings.UPLOAD_MAX_MEGAPIXELS}MP", ok, 0.01)
    if not ok: return False, f"too many pixels ({mp:.1f}MP)", {"mp": mp}, logs
    # 6 min resolution
    t0 = time.perf_counter()
    ok = min(w, h) >= settings.UPLOAD_MIN_SHORT_SIDE
    log("min_resolution", f"{w}x{h}", f"short>={settings.UPLOAD_MIN_SHORT_SIDE}", ok, time.perf_counter() - t0)
    if not ok: return False, f"resolution too low ({w}x{h})", {"w": w, "h": h}, logs
    # 7 blur
    t0 = time.perf_counter()
    try:
        score = _blur_score(im)
    except Exception as e:
        score = -1
    ok = score >= settings.UPLOAD_BLUR_THRESHOLD
    log("blur", f"{score:.1f}", f">={settings.UPLOAD_BLUR_THRESHOLD}", ok, time.perf_counter() - t0)
    if not ok: return False, f"too blurry (laplacian {score:.1f})", {"blur": score}, logs
    # 8 page sanity via detect
    t0 = time.perf_counter()
    try:
        from pipeline.detect import stage_detect
        info, dm = stage_detect(im.convert("RGB"))
        area, second = dm["area_ratio"], dm["second_ratio"]
        ok = area >= 0.05 and second <= 0.9
        # reject two equally-large objects
        if second > 0.9 and area > 0.05:
            ok = False
        log("page_detect", f"area={area} second={second}", "area>=0.05, second<=0.9", ok, time.perf_counter() - t0)
        if not ok: return False, f"page sanity failed (area={area}, second={second})", dm, logs
        measured = {"w": w, "h": h, "mp": mp, "blur": score, "mime": mime, "format": (im.format or "").lower()}
        measured.update(dm)
        return True, "", measured, logs
    except Exception as e:
        log("page_detect", f"error {e}"[:120], "detectable", False, time.perf_counter() - t0)
        return False, f"page detection error: {e}"[:200], {}, logs
