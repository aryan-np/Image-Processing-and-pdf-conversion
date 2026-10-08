"""enhance + compose + merge stages."""
import io
import cv2, numpy as np
from PIL import Image

def stage_enhance(pil_img, enabled=True):
    if not enabled:
        return pil_img, {"enhanced": False}
    arr = np.array(pil_img)
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    out = Image.fromarray(cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2RGB))
    return out, {"enhanced": True, "method": "clahe"}

def stage_compose(pil_img, max_long=2000, quality=82):
    w, h = pil_img.size
    s = min(1.0, max_long / max(w, h))  # never enlarge; ~200 DPI on A4
    if s < 1.0:
        pil_img = pil_img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, "JPEG", quality=quality)
    data = buf.getvalue()
    return data, {"out_w": pil_img.size[0], "out_h": pil_img.size[1], "jpeg_bytes": len(data)}

# merge uses reportlab for A4 layout + pypdf for concat + page numbers
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from pypdf import PdfReader, PdfWriter

MM = 72 / 25.4
PAGE_W, PAGE_H = A4

def _draw_header(c, session_name, title):
    c.setFont("Helvetica-Bold", 11)
    c.drawString(15 * MM, PAGE_H - 12 * MM, f"{session_name} — {title}")
    c.setStrokeGray(0.7)
    c.line(15 * MM, PAGE_H - 14 * MM, PAGE_W - 15 * MM, PAGE_H - 14 * MM)

def _place_image(c, jpeg_bytes, box):
    x, y, bw, bh = box  # y from bottom
    img = ImageReader(io.BytesIO(jpeg_bytes))
    iw, ih = img.getSize()
    s = min(bw / iw, bh / ih)
    dw, dh = iw * s, ih * s
    c.drawImage(img, x + (bw - dw) / 2, y + (bh - dh) / 2, dw, dh, preserveAspectRatio=True)

def build_pages(items, session_name):
    """items: list of dicts {jpeg, caption, layout}. Returns list of pdf-bytes (one per page)."""
    pages = []
    i = 0
    while i < len(items):
        it = items[i]
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=A4)
        top = PAGE_H - 18 * MM
        if it["layout"] == "pair" and i + 1 < len(items) and items[i + 1]["layout"] == "pair":
            nxt = items[i + 1]
            _draw_header(c, session_name, it.get("group", ""))
            half = (top - 15 * MM) / 2
            _place_image(c, it["jpeg"], (15 * MM, top - half, PAGE_W - 30 * MM, half - 8 * MM))
            c.setFont("Helvetica", 9)
            c.drawCentredString(PAGE_W / 2, top - half + 2 * MM, it.get("caption", "Front"))
            _place_image(c, nxt["jpeg"], (15 * MM, 15 * MM, PAGE_W - 30 * MM, half - 8 * MM))
            c.drawCentredString(PAGE_W / 2, 15 * MM - 4 * MM + 8 * MM, nxt.get("caption", "Back"))
            i += 2
        else:
            _draw_header(c, session_name, it.get("group", "") + " / " + it.get("caption", ""))
            _place_image(c, it["jpeg"], (15 * MM, 15 * MM, PAGE_W - 30 * MM, top - 15 * MM - 5 * MM))
            i += 1
        c.showPage(); c.save()
        pages.append(buf.getvalue())
    return pages

def merge_pdfs(pages):
    writer = PdfWriter()
    for p in pages:
        r = PdfReader(io.BytesIO(p))
        for pg in r.pages:
            writer.add_page(pg)
    n = len(writer.pages)
    # page numbers
    for idx, pg in enumerate(writer.pages):
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=A4)
        c.setFont("Helvetica", 8)
        c.drawCentredString(PAGE_W / 2, 10 * MM, f"Page {idx + 1} of {n}")
        c.showPage(); c.save()
        buf.seek(0)
        overlay = PdfReader(buf).pages[0]
        pg.merge_page(overlay)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), n
