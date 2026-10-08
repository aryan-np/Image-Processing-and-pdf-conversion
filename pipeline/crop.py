"""crop stage (Guide Sections 2 steps 5-7).

- 4 corners found AND rectangularity >= 0.90 -> 4-point perspective warp
  (INTER_CUBIC): keystone fixed, ONLY the document kept.
- Otherwise (in range, low rectangularity) -> rotate-only bounding-box warp.
- Edge not found (area <10% or >97%) -> whitespace trim only.
- After warp: shave 1% per side (thin bg slivers), then uniform 2% padding
  so stamps/signatures near the edge survive; raw upload always retained.
- White-background scans: crop to pixels darker than 235/255, keep 1-2%
  margin (minimum 10px).
"""
import cv2, numpy as np
from PIL import Image

from .detect import MIN_AREA, MAX_AREA, RECT_MIN

WARP_PAD_RATIO = 0.02
SHAVE_RATIO = 0.01
TRIM_THRESH = 235
TRIM_PAD_RATIO = 0.02
TRIM_PAD_MIN = 10


def _order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]; rect[2] = pts[np.argmax(s)]
    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]; rect[3] = pts[np.argmax(d)]
    return rect


def _warp(pil_img, quad):
    (tl, tr, br, bl) = quad
    wA = float(np.linalg.norm(br - bl)); wB = float(np.linalg.norm(tr - tl))
    hA = float(np.linalg.norm(tr - br)); hB = float(np.linalg.norm(tl - bl))
    # output size = longer of each pair of opposite edges
    maxW, maxH = max(1, int(max(wA, wB))), max(1, int(max(hA, hB)))
    dst = np.array([[0, 0], [maxW - 1, 0], [maxW - 1, maxH - 1], [0, maxH - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(quad, dst)
    warped = cv2.warpPerspective(np.array(pil_img), M, (maxW, maxH),
                                 flags=cv2.INTER_CUBIC)
    out = Image.fromarray(warped)
    # shave thin bg slivers, then uniform protective padding
    sx, sy = max(1, int(SHAVE_RATIO * maxW)), max(1, int(SHAVE_RATIO * maxH))
    if maxW - 2 * sx > 10 and maxH - 2 * sy > 10:
        out = out.crop((sx, sy, maxW - sx, maxH - sy))
    pad = max(1, int(WARP_PAD_RATIO * max(out.size)))
    out = Image.fromarray(cv2.copyMakeBorder(np.array(out), pad, pad, pad, pad,
                                             cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    return out


def _trim(pil_img, thresh=TRIM_THRESH):
    """Whitespace content-mask trim. Returns (img, trimmed_bool)."""
    w, h = pil_img.size
    gray = np.array(pil_img.convert("L"))
    mask = gray < thresh
    if int(mask.sum()) < 100:
        return pil_img.copy(), False
    ys, xs = np.where(mask)
    pad = max(TRIM_PAD_MIN, int(TRIM_PAD_RATIO * max(w, h)))
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
        out, trimmed = _trim(pil_img)
        return out, {"fallback": "whitespace_trim" if trimmed else "no_crop",
                     "angle": 0.0, "warp": "trim"}
    scale = detect_info["scale"]
    rect_ok = (detect_metrics.get("rectangularity") or 0) >= RECT_MIN
    if (detect_metrics.get("quad_confidence") == 1.0 and rect_ok
            and len(detect_info["approx"]) == 4):
        quad = _order_points(detect_info["approx"].reshape(-1, 2).astype("float32") / scale)
        return _warp(pil_img, quad), {"fallback": "", "angle": detect_metrics.get("angle", 0.0),
                                      "warp": "poly_warp"}
    box = cv2.boxPoints(detect_info["rect"]) / scale
    quad = _order_points(box.astype("float32"))
    return _warp(pil_img, quad), {"fallback": "", "angle": detect_metrics.get("angle", 0.0),
                                  "warp": "rect_warp"}
