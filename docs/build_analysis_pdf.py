"""Build the PoC analysis PDF (quality, risks, recommendation).

Usage: env/bin/python docs/build_analysis_pdf.py [out.pdf]
Default output: docs/poc-analysis-server-built-pdfs.pdf
"""
import sys
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "poc-analysis-server-built-pdfs.pdf"

# Measured 2026-10-08 on synthetic docs (seed 1) through pipeline.process_image.
MEASURED = {
    "clean_scan": {"orig": "1200x1600 (1.92MP, 149KB)", "out": "1200x1600 (1.92MP, 142KB)",
                   "dpi": "137 -> 137", "path": "trim fallback", "ms": 164},
    "photo_dark_bg": {"orig": "1700x2100 (3.57MP, 539KB)", "out": "1234x1628 (2.01MP, 339KB)",
                      "dpi": "180 -> 139", "path": "poly warp", "ms": 99},
}
STAGES = [("load", "~10-15"), ("detect", "~18-25"), ("crop/deskew", "~15-30"),
          ("orientation", "~0.1"), ("align", "~20-25"), ("enhance (CLAHE)", "~15-80"),
          ("compose", "~4-5")]

styles = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=19, leading=23, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13, leading=16, spaceBefore=12, spaceAfter=4)
BODY = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=4)
SMALL = ParagraphStyle("small", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#475569"))
CELL = ParagraphStyle("cell", parent=styles["Normal"], fontSize=9, leading=12)
CELLH = ParagraphStyle("cellh", parent=styles["Normal"], fontSize=9, leading=12, textColor=colors.white)
TITLE = ParagraphStyle("title", parent=styles["Title"], fontSize=24, leading=28)
SUB = ParagraphStyle("sub", parent=styles["Normal"], fontSize=11, leading=14, textColor=colors.HexColor("#475569"))

HDR = [colors.HexColor("#1f4a94"), colors.HexColor("#1f4a94")]
TSTYLE = TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4a94")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c6cede")),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5fb")]),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
])

P = lambda t: Paragraph(t, BODY)
C = lambda t: Paragraph(t, CELL)


def header(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.drawString(15 * mm, A4[1] - 12 * mm, "Fellowship Portal — PoC Analysis: server-built PDFs from phone photos")
    canvas.drawRightString(A4[0] - 15 * mm, A4[1] - 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#e3e8f2"))
    canvas.line(15 * mm, A4[1] - 14 * mm, A4[0] - 15 * mm, A4[1] - 14 * mm)
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(str(OUT), pagesize=A4,
                          leftMargin=15 * mm, rightMargin=15 * mm,
                          topMargin=18 * mm, bottomMargin=15 * mm)
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(
        doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")], onPage=header)])
    story = []
    today = date.today().isoformat()
    story += [Paragraph("Server-built PDFs from phone photos", TITLE),
              Paragraph("PoC analysis after testing: measured quality loss, risks for sensitive "
                        "and multilingual documents, why threshold tuning is slow, and why a "
                        "user-supplied PDF is the better architecture", SUB),
              Spacer(1, 2),
              Paragraph(f"Date: {today} · Suite: 36 passed, 1 skipped (GEN_BACKEND=sync, tesseract 5.5.0) · "
                        "Pipeline: load → detect → crop/deskew → orientation → align → enhance → compose",
                        SMALL),
              Spacer(1, 4)]

    story.append(Paragraph("1. Finding — the PDF images are lower quality than the uploads", H1))
    story.append(P("Each photo passes a chain where every step discards detail. Measured on two sample "
                   "documents through <font face=\"Helvetica-Bold\">pipeline.process_image</font>:"))
    rows = [[Paragraph("<b>sample</b>", CELLH), Paragraph("<b>uploaded</b>", CELLH),
             Paragraph("<b>in PDF</b>", CELLH), Paragraph("<b>eff. DPI on A4</b>", CELLH),
             Paragraph("<b>path / time</b>", CELLH)]]
    for k, v in MEASURED.items():
        rows.append([C(k), C(v["orig"]), C(v["out"]), C(v["dpi"]),
                     C(f"{v['path']}, {v['ms']}ms total")])
    story.append(Table(rows, colWidths=[28 * mm, 42 * mm, 42 * mm, 24 * mm, 39 * mm], style=TSTYLE))
    story.append(Spacer(1, 2))
    for b in [
        "<b>2000px cap (~171 DPI max).</b> Longest side is clamped to 2000px — a 12MP phone photo "
        "is cut to a quarter of its pixels before the PDF is built. Print/archival standard is 300 DPI.",
        "<b>Double JPEG compression.</b> The upload is already JPEG; the pipeline decodes, processes, "
        "and re-encodes at q82. Text edges gain ringing artifacts, compounded on every re-run.",
        "<b>CLAHE always on.</b> Local contrast (clipLimit 2.0) evens lighting but amplifies sensor noise "
        "and can wash out light stamps, pencil marks, and colored seals. No per-image decision.",
        "<b>Three resampling passes.</b> Perspective warp → deskew rotate → final resize (cubic-class "
        "filters, but triple interpolation still softens fine print, MRZ lines, seal micro-text).",
    ]:
        story.append(P("• " + b))
    story.append(P("Verdict: output is a <i>screen-readable facsimile</i>, not a faithful archival copy."))

    story.append(Paragraph("2. Finding — risks for sensitive and multilingual documents", H1))
    rows = [[Paragraph("<b>risk</b>", CELLH), Paragraph("<b>impact</b>", CELLH),
             Paragraph("<b>status in this PoC</b>", CELLH)]]
    for r in [
        ("ID docs on server disk (raw + cache + derivatives + logs), never auto-purged",
         "Breach exposure; retention liability", "No expiry, no access log, no purge"),
        ("Silent alteration (edge stamps clipped, faint signatures bleached) becomes the record",
         "Evidentiary integrity", "2% padding heuristic only; no diff vs raw"),
        ("OSD orientation is Latin-tuned; Devanagari orients worse, lower confidence",
         "Sideways Nepali docs abstain (2.0 gate) or mis-rotate", "Needs tesseract-ocr-nep; untested"),
        ("Mixed-script pages (English + Nepali, e.g. citizenship) worst for OSD + OCR",
         "Garbage text layer, wrong rotation", "No per-language quality signal"),
        ("WARN tells you <i>something</i> happened, not whether text stayed legible",
         "False confidence in bad output", "By design; needs human review step"),
    ]:
        rows.append([C(r[0]), C(r[1]), C(r[2])])
    story.append(Table(rows, colWidths=[62 * mm, 55 * mm, 58 * mm], style=TSTYLE))

    story.append(Paragraph("3. Finding — why threshold tuning on real photos is slow", H1))
    story.append(P("Guide values (area 10–97%, rectangularity ≥0.90, blur ≥100, tilt ≥0.5°, OSD ≥2.0) were "
                   "measured on synthetic images: clean fonts, even light, solid backgrounds. Real photos break "
                   "each assumption, and the thresholds interact:"))
    for b in [
        "Otsu assumes a two-hump brightness histogram — wood grain, shadow, glare, off-white tables make it "
        "one-hump, so the 'paper shape' merges with the table or fractures.",
        "Blur ≥100 shifts with camera shake, noise, and content (guide's own readings: sharp 1121, light blur "
        "211, medium 32, heavy 3) — one number cannot serve all phones.",
        "Rectangularity ≥0.90 rejects curled pages, folded corners, and reflective laminates — common for IDs.",
        "Calibration is a multi-dimensional search: 100+ real photos, per-image metric logs, eyeballing every "
        "rejection — weeks of labeled-data work, and thresholds rot as cameras and habits change.",
    ]:
        story.append(P("• " + b))

    story.append(Paragraph("4. Recommendation — ask the user for a finished PDF", H1))
    for b in [
        "<b>Zero quality loss:</b> uploaded bytes are archived bytes — no DPI cap, re-encode, or CLAHE washout.",
        "<b>Zero tuning burden:</b> layout and legibility are verified by a human eye at upload (preview + "
        "'is this readable?' check beats any Laplacian threshold).",
        "<b>Smaller blast radius:</b> one file instead of raw + cache + derivatives; retention/deletion trivial.",
        "<b>Language-independent:</b> Devanagari, mixed-script, handwritten — all pass through untouched.",
        "<b>No silent alteration:</b> what the applicant signed is what is stored.",
    ]:
        story.append(P("• " + b))
    story.append(P("Suggested shape: PDF upload as the primary path — validate (page count, size, text layer, "
                   "first-page thumbnail + blur check), keep photo→PDF only as an assisted fallback "
                   "('no scanner? photograph each page; quality not guaranteed')."))

    story.append(Paragraph("Appendix — measured stage timings (ms) and reference thresholds", H1))
    rows = [[Paragraph("<b>stage</b>", CELLH), Paragraph("<b>ms range</b>", CELLH)]]
    for s, t in STAGES:
        rows.append([C(s), C(t)])
    story.append(Table(rows, colWidths=[60 * mm, 60 * mm], style=TSTYLE))
    story.append(Spacer(1, 2))
    story.append(Paragraph("Thresholds: warp needs area 10–97% + rectangularity ≥0.90 + 2nd contour ≤40% "
                           "(edge retry Canny 20/60 on failure); deskew at tilt ≥0.5°, limit 45°; OSD accepted "
                           "at confidence ≥2.0; blur ≥100 on 1000px copy; compose longest side 2000px, JPEG q82.",
                           SMALL))
    doc.build(story)
    print("wrote", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    build()
