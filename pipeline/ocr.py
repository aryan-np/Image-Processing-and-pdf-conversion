"""OCR helpers (pure; pytesseract optional)."""
import shutil, time

def tesseract_available():
    return shutil.which("tesseract") is not None

def tesseract_version():
    if not tesseract_available(): return ""
    try:
        import pytesseract
        return str(pytesseract.get_tesseract_version())
    except Exception:
        return ""

def osd_info(pil_img, languages="eng"):
    """Returns dict rotate/confidence/script... raises if unavailable.

    OSD must run with the `osd` model (`-l osd`): the LSTM language models
    (eng, nep, ...) carry no legacy OSD engine and fail with
    "OSD requires a model for the legacy engine". Script identification still
    works under `-l osd`. `languages` is kept for API compatibility and is
    recorded by the caller, not used here.
    """
    import pytesseract
    img = pil_img.copy()
    # 1200px: smaller thumbs starve OSD of characters (800px misreads:
    # upright->180, sideways->90 at junk confidence); 1200px reads 0/270/180
    # correctly at ~0.4s with confidence well above the 2.0 gate.
    img.thumbnail((1200, 1200))
    raw = pytesseract.image_to_osd(img, lang="osd", config="--psm 0")
    rotate, conf, script, script_conf = 0, -1.0, "", -1.0
    for line in raw.splitlines():
        if "Rotate:" in line:
            try: rotate = int(line.split(":")[1].strip())
            except Exception: pass
        if "Orientation confidence:" in line:
            try: conf = float(line.split(":")[1].strip())
            except Exception: pass
        if line.startswith("Script:"):
            try: script = line.split(":")[1].strip()
            except Exception: pass
        if "Script confidence:" in line:
            try: script_conf = float(line.split(":")[1].strip())
            except Exception: pass
    return {"raw": raw, "rotate": rotate, "confidence": conf, "script": script,
            "script_conf": script_conf, "thumb": img.size}

def full_ocr(pil_img, languages="eng"):
    import pytesseract
    img = pil_img.copy()
    img.thumbnail((1600, 1600))
    t0 = time.perf_counter()
    try:
        data = pytesseract.image_to_data(img, lang=languages, output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(img, lang=languages)
    except Exception:
        # fallback to first language
        lang = languages.split("+")[0]
        data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(img, lang=lang)
    dt = (time.perf_counter() - t0) * 1000
    words, confs = [], []
    n = len(data.get("text", []))
    for k in range(n):
        t = (data["text"][k] or "").strip()
        try: cf = float(data["conf"][k])
        except Exception: cf = -1
        if t:
            words.append({"text": t, "conf": cf,
                          "bbox": [data["left"][k], data["top"][k], data["width"][k], data["height"][k]]})
            if cf >= 0: confs.append(cf)
    mean_c = sum(confs) / len(confs) if confs else -1
    return {"text": text, "words": words, "mean_conf": mean_c,
            "min_conf": min(confs) if confs else -1, "word_count": len(words),
            "ms": dt, "size": img.size}
