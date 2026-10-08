"""orientation stage: 0/90/180/270 via aspect heuristic + optional Tesseract OSD."""
import shutil

def _tesseract_available():
    return shutil.which("tesseract") is not None

def stage_orientation(pil_img, expected="any", ocr_mode="OFF", ocr_fn=None,
                      ocr_fallback_enabled=True):
    """ocr_fn: callable(pil_img)-> dict with rotate/confidence, to keep this module Django-free.

    ocr_fallback_enabled is the OCR_FALLBACK_ENABLED kill-switch: when False,
    FALLBACK mode never triggers OCR (behaves like OFF). ALWAYS is unaffected.
    """
    w, h = pil_img.size
    method, rotation, osd_conf = "exif", 0, None
    ambiguous = False
    if expected == "portrait" and w > h:
        rotation, method, ambiguous = 90, "aspect", True
    elif expected == "landscape" and h > w:
        rotation, method, ambiguous = 90, "aspect", True
    else:
        # short-side portrait docs photographed sideways: can't know without OCR; mark ambiguous
        ambiguous = True if expected == "any" else False
    # decide OCR need
    fallback_allowed = ocr_fallback_enabled and (ocr_mode == "FALLBACK")
    need_ocr = _tesseract_available() and ocr_fn is not None and (
        (ocr_mode == "ALWAYS") or (fallback_allowed and ambiguous))
    osd_info = None
    if need_ocr:
        try:
            osd_info = ocr_fn(pil_img)
            rot = int(osd_info.get("rotate", 0) or 0)
            osd_conf = float(osd_info.get("confidence", -1))
            if ocr_mode == "ALWAYS":
                rotation, method = rot, "osd"
            elif ocr_mode == "FALLBACK" and ambiguous:
                # Guide step 8: accept OSD at confidence 2.0+; below 2.0
                # leave the image as is and flag (do not guess).
                if osd_conf >= 2.0 and rot in (90, 180, 270):
                    rotation, method = rot, "osd_fallback"
                elif osd_conf >= 2.0:
                    method = "aspect_or_none"
                else:
                    method = "osd_low_conf"
        except Exception as e:
            osd_info = {"error": str(e)}
    out = pil_img
    if rotation == 90: out = pil_img.transpose(3)  # ROTATE_270? use expand
    if rotation == 90:
        out = pil_img.rotate(-90, expand=True)
    elif rotation == 180:
        out = pil_img.rotate(180, expand=True)
    elif rotation == 270:
        out = pil_img.rotate(90, expand=True)
    metrics = {"method": method, "rotation": rotation, "osd_confidence": osd_conf,
               "ambiguous": ambiguous, "osd": osd_info,
               "fallback_gated": (ocr_mode == "FALLBACK" and not ocr_fallback_enabled)}
    return out, metrics
