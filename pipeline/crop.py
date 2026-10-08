"""crop stage (spec 5.1/5.2).

- High-confidence quad (4-corner approx) -> 4-point perspective warp:
  keystone fixed, ONLY the document kept, +2% padding.
- Weaker quad but sane area (30-98%) -> minAreaRect warp + 2% padding.
- Edge not found (area <30% or >98%, e.g. white paper on white surface)
  -> whitespace content-mask trim only (never a warp).
- ~2% padding everywhere so stamps/signatures near the edge survive;
  the raw upload is always retained upstream.
"""
import cv2, numpy as np
from PIL import Image

MIN_AREA = 0.30
MAX_AREA = 0.98
PAD_RATIO = 0.02


def _order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]; rect[2] = pts[np.argmax(s)]
    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]; rect[3] = pts[np.argmax(d)]
    return rect


def _warp(pil_img, quad, pad_ratio=PAD_RATIO):
    (tl, tr, br, bl) = quad
    wA = float(np.linalg.norm(br - bl)); wB = float(np.linalg.norm(tr - tl))
    hA = float(np.linalg.norm(tr - br)); hB = float(np.linalg.norm(tl - bl))
    maxW, maxH = max(1, int(max(wA, wB))), max(1, int(max(hA, hB)))
    dst = np.array([[0, 0], [maxW - 1, 0], [maxW - 1, maxH - 1], [0, maxH - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(quad, dst)
    warped = cv2.warpPerspective(np.array(pil_img), M, (maxW, maxH))
    out = Image.fromarray(warped)
    pad = max(1, int(pad_ratio * max(maxW, maxH)))
    out = Image.fromarray(cv2.copyMakeBorder(np.array(out), pad, pad, pad, pad,
                                             cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    return out


def _trim(pil_img, thresh=235):
    """Whitespace content-mask trim. Returns (img, trimmed_bool)."""
    w, h = pil_img.size
    gray = np.array(pil_img.convert("L"))
    mask = gray < thresh
    if int(mask.sum()) < 100:
        return pil_img.copy(), False
    ys, xs = np.where(mask)
    pad = int(0.02 * max(w, h))
    x0, x1 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + pad)
    y0, y1 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + pad)
    if x1 > x0 and y1 > y0:
        return pil_img.crop((x0, y0, x1, y1)), True
    return pil_img.copy(), False


def stage_crop_deskew(pil_img, detect_info=None, detect_metrics=None):
    """Returns (pil_img, metrics). Backwards compatible: re-detects when
    detect args are omitted."""
    if detect_info is None or detect_metrics is None:
        from .detect import stage_detect
        detect_info, detect_metrics = stage_detect(pil_img)
    area_ratio = detect_metrics.get("area_ratio", 0)
    if detect_info is None or not (MIN_AREA <= area_ratio <= MAX_AREA):
        # edge not found (white-on-white / full-bleed / tiny doc): trim only
        if detect_info is None:
            out, trimmed = _trim(pil_img)
            fallback = "no_crop" if not trimmed else "whitespace_trim"
        else:
            out, trimmed = _trim(pil_img)
            fallback = "whitespace_trim" if trimmed else "no_crop"
        return out, {"fallback": fallback, "angle": 0.0, "warp": "trim"}
    scale = detect_info["scale"]
    if detect_metrics.get("quad_confidence") == 1.0 and len(detect_info["approx"]) == 4:
        # high-confidence quad -> true 4-corner perspective warp
        quad = _order_points(detect_info["approx"].reshape(-1, 2).astype("float32") / scale)
        return _warp(pil_img, quad), {"fallback": "", "angle": detect_metrics.get("angle", 0.0),
                                      "warp": "poly_warp"}
    # weaker quad -> bounding-rectangle warp
    box = cv2.boxPoints(detect_info["rect"]) / scale
    quad = _order_points(box.astype("float32"))
    return _warp(pil_img, quad), {"fallback": "", "angle": detect_metrics.get("angle", 0.0),
                                  "warp": "rect_warp"}
