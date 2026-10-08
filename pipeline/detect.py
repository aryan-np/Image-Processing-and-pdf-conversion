"""detect stage: find largest paper-like contour (spec 5.1/5.2).

Downscale to ~1000px, grayscale, blur, Otsu threshold, morphological close,
then contours of the BRIGHT (paper) regions with RETR_EXTERNAL.
Largest bright blob = the page.
- area <30% or >98% of the frame -> edge not found -> whitespace trim only.
- second_ratio = 2nd largest / largest; upload rejects when >~40% (two objects).
"""
import cv2, numpy as np
from PIL import Image

TARGET_LONG = 1000
MIN_AREA = 0.30   # below -> no clean page quad -> trim only
MAX_AREA = 0.98   # above -> full-bleed / edge not found -> trim only
SECOND_REJECT = 0.40  # 2nd contour above this fraction -> ambiguous (upload rejects)


def _full_bleed(scale, reason):
    return None, {"area_ratio": 0.99, "angle": 0.0, "second_ratio": 0.0,
                  "quad_confidence": 0.5, "scale": scale, "full_bleed": True,
                  "bg_dark": False, "reason": reason}


def stage_detect(pil_img, target_long=TARGET_LONG):
    w, h = pil_img.size
    scale = min(1.0, target_long / max(w, h))
    small = pil_img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    gray = np.array(small.convert("L"))
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # background type for logs: sample the frame border (the photo surface,
    # not the document). Dark border = photo on a dark surface.
    gh, gw = gray.shape
    bh, bw = max(1, gh // 33), max(1, gw // 33)
    border = np.concatenate([gray[:bh, :].ravel(), gray[-bh:, :].ravel(),
                            gray[:, :bw].ravel(), gray[:, -bw:].ravel()])
    bg_dark = bool((border < 128).mean() > 0.5)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    # bright (paper) regions, outer contours only: holes (text/stamps) ignored
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = gray.shape[0] * gray.shape[1]
    scored = sorted(((cv2.contourArea(c), c) for c in contours), reverse=True)
    if not scored:
        # full-bleed / clean scan: no contours -> treat as full page
        return _full_bleed(scale, "no_contours")
    largest, cnt = scored[0]
    area_ratio = largest / max(1, img_area)
    if area_ratio < 0.03:
        # full-bleed white doc: largest contour tiny -> full page
        return _full_bleed(scale, "tiny_contour")
    second_ratio = (scored[1][0] / largest) if len(scored) > 1 else 0.0
    rect = cv2.minAreaRect(cnt)
    angle = float(rect[2])
    peri = cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
    quad_conf = 1.0 if len(approx) == 4 else (0.7 if len(approx) in (3, 5, 6) else 0.4)
    info = {"contour": cnt, "rect": rect, "approx": approx, "scale": scale,
            "small_size": [gray.shape[1], gray.shape[0]]}
    metrics = {"area_ratio": round(float(area_ratio), 4), "angle": round(float(angle), 2),
               "second_ratio": round(float(second_ratio), 4),
               "quad_confidence": quad_conf, "scale": scale, "bg_dark": bg_dark,
               "second_over": bool(second_ratio > SECOND_REJECT)}
    return info, metrics
