"""crop_deskew stage."""
import cv2, numpy as np
from PIL import Image

def _order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]; rect[2] = pts[np.argmax(s)]
    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]; rect[3] = pts[np.argmax(d)]
    return rect

def stage_crop_deskew(pil_img, detect_info, detect_metrics):
    w, h = pil_img.size
    area_ratio = detect_metrics.get("area_ratio", 0)
    fallback = ""
    if detect_info is None or not (0.30 <= area_ratio <= 0.98):
        # whitespace trim fallback
        gray = np.array(pil_img.convert("L"))
        mask = gray < 235
        if mask.sum() < 100:
            out = pil_img.copy()
            fallback = "no_crop"
        else:
            ys, xs = np.where(mask)
            pad = int(0.02 * max(w, h))
            x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad)
            y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad)
            out = pil_img.crop((x0, y0, x1, y1)) if (x1 > x0 and y1 > y0) else pil_img.copy()
            fallback = "whitespace_trim"
        return out, {"fallback": fallback, "angle": 0.0}
    scale = detect_info["scale"]
    box = cv2.boxPoints(detect_info["rect"]) / scale
    box = _order_points(box.astype("float32"))
    (tl, tr, br, bl) = box
    wA = float(np.linalg.norm(br - bl)); wB = float(np.linalg.norm(tr - tl))
    hA = float(np.linalg.norm(tr - br)); hB = float(np.linalg.norm(tl - bl))
    maxW, maxH = max(1, int(max(wA, wB))), max(1, int(max(hA, hB)))
    # upright: keep longer side vertical if source was portrait-ish
    dst = np.array([[0, 0], [maxW - 1, 0], [maxW - 1, maxH - 1], [0, maxH - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(box, dst)
    warped = cv2.warpPerspective(np.array(pil_img), M, (maxW, maxH))
    out = Image.fromarray(warped)
    pad = int(0.02 * max(maxW, maxH))
    out = Image.fromarray(cv2.copyMakeBorder(np.array(out), pad, pad, pad, pad,
                                             cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    return out, {"fallback": fallback, "angle": detect_metrics.get("angle", 0.0)}
