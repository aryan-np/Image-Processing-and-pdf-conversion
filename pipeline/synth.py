"""Synthetic document image generator. Seeded, Pillow/numpy/cv2 only."""
import io, random
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCENARIOS = ["clean_scan", "photo_dark_bg", "photo_wood_like_bg", "perspective",
             "sideways_90", "upside_down_180", "white_on_white", "low_res",
             "blurry", "huge_12mp", "png_and_webp", "two_objects", "corrupt_file"]

# Friendly label + whether the scenario is expected to FAIL upload validation.
SCENARIO_META = {
    "clean_scan": ("Clean scan", False),
    "photo_dark_bg": ("Photo on dark background", False),
    "photo_wood_like_bg": ("Photo on wood background", False),
    "perspective": ("Perspective / keystone", False),
    "sideways_90": ("Sideways 90°", False),
    "upside_down_180": ("Upside down 180°", False),
    "white_on_white": ("White on white (rejected: darker surface needed)", True),
    "low_res": ("Low resolution (rejected)", True),
    "blurry": ("Blurry (rejected)", True),
    "huge_12mp": ("Huge 12MP (slow)", False),
    "png_and_webp": ("PNG / WebP mix", False),
    "two_objects": ("Two objects (rejected)", True),
    "corrupt_file": ("Corrupt file (rejected)", True),
}

LOREM = ["Name: Test User", "Doc No: 12-34-56-78901", "Issued: 2024-01-15",
         "Address: Kathmandu, Nepal", "Ref: FELLOW-2026-0042", "Signature: ______"]

def _base_doc(w=1200, h=1600, title="TEST DOCUMENT", rng=None, stamp=False, portrait_card=False):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, w - 8, h - 8], outline="black", width=4)
    d.text((40, 40), title, fill="black")
    y = 120
    for line in LOREM:
        d.text((40, y), line, fill=(30, 30, 30)); y += 45
    if portrait_card:
        d.rectangle([w - 320, 120, w - 80, 420], outline="gray", width=3)
        d.text((w - 300, 250), "PHOTO", fill="gray")
    if stamp:
        d.ellipse([w - 320, h - 300, w - 80, h - 100], outline=(180, 40, 40), width=5)
        d.text((w - 300, h - 220), "STAMP", fill=(180, 40, 40))
    return img

def _paste_on_bg(doc, bg_color=(40, 40, 50), angle=6, rng=None, texture=False):
    rng = rng or random.Random(0)
    bw, bh = doc.size[0] + 500, doc.size[1] + 500
    if texture:
        bg = np.zeros((bh, bw, 3), np.uint8)
        for i in range(0, bh, 6):
            bg[i, :] = (90 + rng.randint(-15, 15), 60 + rng.randint(-12, 12), 35 + rng.randint(-10, 10))
        bg = Image.fromarray(bg)
    else:
        bg = Image.new("RGB", (bw, bh), bg_color)
    rot = doc.rotate(angle, expand=True, fillcolor=(0, 0, 0))
    bg.paste(rot, ((bw - rot.size[0]) // 2, (bh - rot.size[1]) // 2))
    noise = np.random.default_rng(rng.randint(0, 999999)).integers(-8, 8, (bh, bw, 1), np.int16)
    arr = np.clip(np.array(bg).astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)

def _perspective(img, rng):
    w, h = img.size
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    k = 0.06
    dst = np.float32([[w * k, h * k * 0.4], [w * (1 - k * 1.4), 0], [w, h], [0, h * (1 - k * 0.3)]])
    M = cv2.getPerspectiveTransform(src, dst)
    return Image.fromarray(cv2.warpPerspective(np.array(img), M, (w, h), borderValue=(255, 255, 255)))

def generate(scenario, seed=1, title="TEST DOCUMENT", fmt="JPEG"):
    rng = random.Random(f"{seed}-{scenario}-{title}")
    if scenario == "corrupt_file":
        return b"this is not an image at all\x00\x01\x02", "bin"
    if scenario == "low_res":
        img = _base_doc(300, 400, title, rng)
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=80)
        return buf.getvalue(), "jpg"
    if scenario == "blurry":
        img = _base_doc(1200, 1600, title, rng)
        img = img.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(8))
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=80)
        return buf.getvalue(), "jpg"
    if scenario == "white_on_white":
        img = Image.new("RGB", (1200, 1600), "white")
        d = ImageDraw.Draw(img)
        d.text((100, 700), "faint text here", fill=(250, 250, 250))
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=80)
        return buf.getvalue(), "jpg"
    if scenario == "two_objects":
        img = Image.new("RGB", (1600, 1200), (30, 30, 30))
        d1 = _base_doc(600, 800, title + " A", rng)
        d2 = _base_doc(600, 800, title + " B", rng)
        img.paste(d1, (80, 150)); img.paste(d2, (900, 150))
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=80)
        return buf.getvalue(), "jpg"
    if scenario == "huge_12mp":
        img = _base_doc(3000, 4000, title, rng, stamp=True)
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=82)
        return buf.getvalue(), "jpg"
    base = _base_doc(1200, 1600, title, rng, stamp=(scenario != "clean_scan"))
    if scenario == "clean_scan":
        img = base
    elif scenario == "photo_dark_bg":
        img = _paste_on_bg(base, angle=rng.uniform(2, 12), rng=rng)
    elif scenario == "photo_wood_like_bg":
        img = _paste_on_bg(base, angle=rng.uniform(2, 12), rng=rng, texture=True)
    elif scenario == "perspective":
        img = _perspective(_paste_on_bg(base, angle=2, rng=rng), rng)
    elif scenario == "sideways_90":
        img = base.rotate(-90, expand=True)
    elif scenario == "upside_down_180":
        img = base.rotate(180, expand=True)
    else:
        img = base
    if scenario == "png_and_webp":
        fmt = rng.choice(["PNG", "WEBP"])
    buf = io.BytesIO()
    if fmt == "PNG": img.save(buf, "PNG")
    elif fmt == "WEBP": img.save(buf, "WEBP", quality=85)
    else: img.save(buf, "JPEG", quality=85)
    ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[fmt]
    return buf.getvalue(), ext

def pick_scenario(mix: dict, rng: random.Random):
    total = sum(mix.values())
    r = rng.uniform(0, total)
    acc = 0
    for k, v in mix.items():
        acc += v
        if r <= acc: return k
    return next(iter(mix))

def parse_mix(s: str):
    mix = {}
    for part in (s or "").split(","):
        part = part.strip()
        if not part: continue
        if "=" in part:
            k, v = part.split("=", 1); mix[k.strip()] = float(v)
        else:
            mix[part] = 1
    return mix or {"clean_scan": 100}
