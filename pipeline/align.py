"""align stage: fine skew (deskew) correction for small tilt angles.

Coarse geometry is already handled upstream:
  load      -> EXIF transpose
  detect    -> largest page contour + angle (coarse)
  crop      -> perspective warp of the page quad (keystone fix)
  orient    -> 0/90/180/270 via aspect heuristic + optional Tesseract OSD

What was missing: sub-degree / few-degree skew (e.g. photo taken slightly
tilted, or full-bleed scan rotated 2-10 deg where detect falls back to
whitespace_trim and applies NO deskew). This stage estimates the dominant
text-line angle via Canny + probabilistic Hough and counter-rotates.

Django-free. Pure Pillow + numpy + cv2. Never raises on bad input:
low-confidence cases return the image unchanged with aligned=False.
"""
import math
import cv2
import numpy as np
from PIL import ImageOps

# Thresholds (also reported in logs / image-detail so the user can see them).
MIN_ANGLE = 0.3   # ignore skew below this (noise floor), degrees
MAX_ANGLE = 15.0  # never correct more than this (likely a mis-detection)
WHITE_THRESH = 240  # pixels >= this count as blank margin when re-squaring
PAD_RATIO = 0.02  # uniform white padding re-applied after deskew


def _estimate_skew_deg(gray_small):
    """Return (skew_deg, n_lines, method) or (0.0, 0, 'none')."""
    h, w = gray_small.shape
    blur = cv2.GaussianBlur(gray_small, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150, apertureSize=3)
    min_len = max(80, int(w * 0.25))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=120,
                            minLineLength=min_len, maxLineGap=12)
    if lines is None or len(lines) < 5:
        return 0.0, 0 if lines is None else len(lines), "hough_none"
    lines = np.asarray(lines).reshape(-1, 4)  # cv2 4.x: (N,1,4); 5.x: (N,4)
    angles = []
    for x1, y1, x2, y2 in lines:
        dx, dy = float(x2 - x1), float(y2 - y1)
        if dx == 0 and dy == 0:
            continue
        a = math.degrees(math.atan2(dy, dx))
        # fold to [-45, 45]: near-vertical lines belong to the other axis
        if a < -45:
            a += 90
        elif a > 45:
            a -= 90
        if abs(a) <= 15:  # near-horizontal only (post-orientation text lines)
            angles.append(a)
    if len(angles) < 5:
        return 0.0, len(angles), "hough_few"
    angles.sort()
    skew = float(angles[len(angles) // 2])  # median, robust to outliers
    spread = float(angles[int(len(angles) * 0.75)] - angles[int(len(angles) * 0.25)])
    if spread > 5.0:
        # lines disagree -> unreliable, don't touch
        return 0.0, len(angles), "hough_spread"
    return skew, len(angles), "hough"


def _fallback_rect_angle(gray_small):
    """Coarse backup: angle of largest foreground blob via minAreaRect."""
    _, th = cv2.threshold(gray_small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if (th == 0).mean() > 0.6:
        th = 255 - th
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(255 - closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0, "rect_none"
    cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(cnt) < 0.03 * gray_small.size:
        return 0.0, "rect_small"
    angle = float(cv2.minAreaRect(cnt)[2])  # (-90, 0]
    # normalize to [-45, 45]
    if angle < -45:
        angle += 90
    return angle, "rect"


def _trim_and_repad(pil_img, white_thresh=WHITE_THRESH, pad_ratio=PAD_RATIO):
    """Re-square the canvas after a rotation.

    rotate(expand=True) leaves lopsided white triangles + the old (now
    rotated) padding border. Trim back to the non-white content bbox, then
    add a fresh uniform white pad so borders/padding are axis-aligned again.
    Returns (img, did_repad).
    """
    gray = np.array(pil_img.convert("L"))
    mask = gray < white_thresh
    if int(mask.sum()) < 100:
        return pil_img, False
    ys, xs = np.where(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    if x1 <= x0 or y1 <= y0:
        return pil_img, False
    trimmed = pil_img.crop((x0, y0, x1 + 1, y1 + 1))
    pad = max(1, int(pad_ratio * max(trimmed.size)))
    return ImageOps.expand(trimmed, border=pad, fill=(255, 255, 255)), True


def _measure(pil_img, max_side=1000):
    """Estimate skew of an image. Returns (skew, n_lines, method)."""
    w, h = pil_img.size
    scale = min(1.0, max_side / max(w, h))
    small = pil_img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    return _estimate_skew_deg(np.array(small.convert("L")))


def stage_align(pil_img, max_side=1000, min_angle=MIN_ANGLE, max_angle=MAX_ANGLE):
    """Counter-rotate small skew. Returns (pil_img, metrics).

    metrics: {skew_angle (original tilt), corrected_deg, residual_skew
    (processed tilt, re-measured on the output), aligned(bool), method,
    n_lines, repad}
    Positive skew_angle = text lines slope down to the right in image
    coords; correction rotates by -skew (PIL CCW convention keeps lines level).
    When a correction is applied the canvas is re-squared (trim + uniform
    2% white pad) so borders/padding are not left rotated.
    """
    w, h = pil_img.size
    scale = min(1.0, max_side / max(w, h))
    small = pil_img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    gray = np.array(small.convert("L"))

    skew, n_lines, method = _estimate_skew_deg(gray)
    if method in ("hough_none", "hough_few"):
        # try contour-rect backup before giving up
        r_angle, r_method = _fallback_rect_angle(gray)
        if r_method == "rect" and min_angle <= abs(r_angle) <= max_angle:
            skew, method = r_angle, "rect_fallback"
        else:
            skew, method = 0.0, f"{method}+{r_method}"

    base = {"skew_angle": round(float(skew), 3), "n_lines": int(n_lines)}
    if abs(skew) < min_angle or abs(skew) > max_angle:
        # untouched: processed tilt == original tilt
        return pil_img, {**base, "corrected_deg": 0.0, "aligned": False,
                         "method": method if skew == 0 else method + "_rejected",
                         "residual_skew": round(float(skew), 3), "repad": False}
    # PIL rotate() is counter-clockwise; image y-axis points down so a line
    # sloping down-right (positive skew) needs a CCW (+skew) rotation to level.
    # Verified empirically: +7deg tilted doc -> skew ~= +7 -> rotate(+7) straightens.
    out = pil_img.rotate(skew, expand=True, fillcolor=(255, 255, 255))
    out, repad = _trim_and_repad(out)
    # re-measure on the processed image: leftover tilt after the fix
    residual, _, _ = _measure(out, max_side)
    return out, {**base, "corrected_deg": round(float(skew), 3),
                 "aligned": True, "method": method,
                 "residual_skew": round(float(residual), 3), "repad": repad}
