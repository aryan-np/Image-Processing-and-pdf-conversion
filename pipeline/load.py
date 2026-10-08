"""load stage: bytes -> RGB PIL image. No Django imports."""
import io
from PIL import Image, ImageOps

def stage_load(data: bytes):
    img = Image.open(io.BytesIO(data))
    fmt = (img.format or "").lower()
    exif_ori = 1
    try:
        exif = img.getexif()
        exif_ori = int(exif.get(274, 1)) if exif else 1
    except Exception:
        exif_ori = 1
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    w, h = img.size
    metrics = {"format": fmt, "exif_orientation": exif_ori, "orig_w": w, "orig_h": h,
               "megapixels": round(w * h / 1e6, 3)}
    return img, metrics
