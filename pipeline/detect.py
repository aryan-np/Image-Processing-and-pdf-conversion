"""detect stage: find largest page-like contour."""
import cv2, numpy as np
from PIL import Image

def stage_detect(pil_img, target_long=1000):
    w, h = pil_img.size
    scale = min(1.0, target_long / max(w, h))
    small = pil_img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    gray = np.array(small.convert("L"))
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # document is dark-on-white usually; invert if mostly dark
    if (th == 0).mean() > 0.6:
        th = 255 - th
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(255 - closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = gray.shape[0] * gray.shape[1]
    scored = sorted(((cv2.contourArea(c), c) for c in contours), reverse=True)
    if not scored:
        # full-bleed / clean scan: no contours -> treat as full page
        return None, {"area_ratio": 0.99, "angle": 0.0, "second_ratio": 0.0,
                       "quad_confidence": 0.5, "scale": scale, "full_bleed": True}
    largest, cnt = scored[0]
    area_ratio = largest / max(1, img_area)
    if area_ratio < 0.03:
        # full-bleed white doc: largest contour tiny -> full page
        return None, {"area_ratio": 0.99, "angle": 0.0, "second_ratio": 0.0,
                       "quad_confidence": 0.5, "scale": scale, "full_bleed": True}
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
               "quad_confidence": quad_conf, "scale": scale}
    return info, metrics
