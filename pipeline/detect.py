"""detect stage: find largest paper-like contour (Guide Sections 2-4).

Step 2: downscale to ~1000px longest side (fast, less noise); scale is kept
to map points back to full size.
Step 3: grayscale, blur 5x5, Otsu split, close kernel ~1.5% of longest side
(15px at 1000px) so the paper is one shape; contours of the BRIGHT (paper)
regions, RETR_EXTERNAL (text/stamp holes ignored). Largest bright blob = page.
Step 3b: if the brightness split fails (shape >97% of frame, e.g. light or
near-white background) retry on edges: blur 7x7, Canny(20, 60), close ~2.5%.
Step 4: sheet checks -> area 10-97%, rectangularity >= 0.90, second-largest
shape <= 40% of the largest. Outside 10-97%: edge not found -> trim only.
"""
import cv2, numpy as np
from PIL import Image

TARGET_LONG = 1000
CLOSE_RATIO = 0.015   # step 3 close kernel as fraction of longest side
EDGE_CLOSE_RATIO = 0.025  # step 3b close kernel fraction
MIN_AREA = 0.10   # below -> no clean page quad -> trim only
MAX_AREA = 0.97   # above -> edge not found -> trim only (3b retry first)
RECT_MIN = 0.90   # rectangularity gate for warp/upload
SECOND_REJECT = 0.40  # 2nd contour above this fraction -> ambiguous (upload rejects)


def _odd(k):
    k = max(3, int(k))
    return k if k % 2 else k + 1


def _describe(cnt, img_area, scale, small_size, extra=None):
    largest = cv2.contourArea(cnt)
    area_ratio = largest / max(1, img_area)
    rect = cv2.minAreaRect(cnt)
    angle = float(rect[2])
    rw, rh = rect[1]
    rect_area = max(1.0, float(rw) * float(rh))
    rectangularity = min(1.0, float(largest) / rect_area)
    peri = cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)  # tolerance 2% of outline
    quad_conf = 1.0 if len(approx) == 4 else (0.7 if len(approx) in (3, 5, 6) else 0.4)
    info = {"contour": cnt, "rect": rect, "approx": approx, "scale": scale,
            "small_size": small_size}
    metrics = {"area_ratio": round(float(area_ratio), 4), "angle": round(float(angle), 2),
               "rectangularity": round(float(rectangularity), 4),
               "quad_confidence": quad_conf, "scale": scale}
    if extra:
        metrics.update(extra)
    return info, metrics


def _full_bleed(scale, reason):
    return None, {"area_ratio": 0.99, "angle": 0.0, "second_ratio": 0.0,
                  "rectangularity": None, "quad_confidence": 0.5, "scale": scale,
                  "full_bleed": True, "bg_dark": False, "reason": reason,
                  "detect_method": "none"}


def _edge_fallback(gray, scale, small_size):
    """Step 3b: find the visible paper edge line instead of brightness."""
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    edges = cv2.Canny(blur, 20, 60)
    k = _odd(EDGE_CLOSE_RATIO * max(gray.shape))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = gray.shape[0] * gray.shape[1]
    scored = sorted(((cv2.contourArea(c), c) for c in contours), reverse=True)
    if not scored:
        return None, None
    largest, cnt = scored[0]
    if not (MIN_AREA <= largest / max(1, img_area) <= MAX_AREA):
        return None, None
    info, metrics = _describe(cnt, img_area, scale, small_size,
                              {"detect_method": "edges", "edge_kernel": k})
    if metrics["rectangularity"] < RECT_MIN:
        return None, None
    second = (scored[1][0] / largest) if len(scored) > 1 else 0.0
    metrics["second_ratio"] = round(float(second), 4)
    metrics["second_over"] = bool(second > SECOND_REJECT)
    return info, metrics


def stage_detect(pil_img, target_long=TARGET_LONG):
    w, h = pil_img.size
    scale = min(1.0, target_long / max(w, h))
    small = pil_img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    gray = np.array(small.convert("L"))
    small_size = [gray.shape[1], gray.shape[0]]
    # background type for logs: sample the frame border (photo surface, not doc)
    gh, gw = gray.shape
    bh, bw = max(1, gh // 33), max(1, gw // 33)
    border = np.concatenate([gray[:bh, :].ravel(), gray[-bh:, :].ravel(),
                            gray[:, :bw].ravel(), gray[:, -bw:].ravel()])
    bg_dark = bool((border < 128).mean() > 0.5)

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = _odd(CLOSE_RATIO * max(gray.shape))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = gray.shape[0] * gray.shape[1]
    scored = sorted(((cv2.contourArea(c), c) for c in contours), reverse=True)
    if not scored:
        return _full_bleed(scale, "no_contours")
    largest, cnt = scored[0]
    if largest / max(1, img_area) > MAX_AREA:
        # brightness split failed (paper not separated from background):
        # retry on edges before giving up (step 3b)
        einfo, emetrics = _edge_fallback(gray, scale, small_size)
        if einfo is not None:
            emetrics["bg_dark"] = bg_dark
            emetrics["close_kernel"] = k
            return einfo, emetrics
        return _full_bleed(scale, "edge_retry_failed")
    if largest / max(1, img_area) < 0.03:
        return _full_bleed(scale, "tiny_contour")
    second_ratio = (scored[1][0] / largest) if len(scored) > 1 else 0.0
    info, metrics = _describe(cnt, img_area, scale, small_size,
                              {"detect_method": "brightness", "close_kernel": k})
    metrics.update({"second_ratio": round(float(second_ratio), 4),
                    "second_over": bool(second_ratio > SECOND_REJECT),
                    "bg_dark": bg_dark})
    return info, metrics
