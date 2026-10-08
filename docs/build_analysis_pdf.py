"""Build the PoC analysis PDF (quality, risks, recommendation).

Usage: env/bin/python docs/build_analysis_pdf.py [out.pdf]
Default output: docs/poc-analysis-server-built-pdfs.pdf

All numbers below were measured 2026-10-08 by running the real pipeline
(pipeline.process_image); the walkthrough in section 3 quotes actual log lines.
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

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("title", parent=styles["Title"], fontSize=24, leading=28)
SUB = ParagraphStyle("sub", parent=styles["Normal"], fontSize=11, leading=14,
                     textColor=colors.HexColor("#475569"))
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=17, leading=21, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, leading=15, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=4)
SMALL = ParagraphStyle("small", parent=styles["Normal"], fontSize=8.5, leading=11,
                       textColor=colors.HexColor("#475569"))
CELL = ParagraphStyle("cell", parent=styles["Normal"], fontSize=9, leading=12)
CELLH = ParagraphStyle("cellh", parent=styles["Normal"], fontSize=9, leading=12,
                       textColor=colors.white)
LOG = ParagraphStyle("log", parent=styles["Normal"], fontSize=8.5, leading=12,
                     textColor=colors.HexColor("#cfe3ff"))

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
    # Log quotes render as single-cell tables (deterministic height — a bare
    # Paragraph with backColor can mis-measure wrapped lines and overlap).
    LOGTABLE = TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0f1728")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])

    def L(t):
        return Table([[Paragraph(t, LOG)]], colWidths=[doc.width], style=LOGTABLE)
    story = []
    today = date.today().isoformat()
    story += [Paragraph("Server-built PDFs from phone photos", TITLE),
              Paragraph("PoC analysis after testing: where quality is lost, what each threshold does "
                        "(with measured examples), risks for sensitive and multilingual documents, "
                        "what to improve, and the recommended architecture", SUB),
              Spacer(1, 2),
              Paragraph(f"Date: {today} · Suite: 36 passed, 1 skipped (GEN_BACKEND=sync, tesseract 5.5.0) · "
                        "Pipeline: load → detect → crop/deskew → orientation → align → enhance → compose",
                        SMALL),
              Spacer(1, 4)]

    story.append(Paragraph("1. Formats in, format out — and where quality is lost", H1))
    story.append(P("Uploads accept <b>JPG, PNG and WebP</b> (HEIC only if enabled). But whatever comes in, "
                   "the PDF is always built from <b>fresh JPEGs at quality 82</b>. So a perfect PNG upload is "
                   "still decoded, processed, and re-compressed as lossy JPEG — that conversion alone softens "
                   "text edges, and it happens on every single run:"))
    rows = [[Paragraph("<b>sample</b>", CELLH), Paragraph("<b>uploaded</b>", CELLH),
             Paragraph("<b>stored in PDF</b>", CELLH), Paragraph("<b>eff. DPI on A4</b>", CELLH)]]
    for r in [
        ("clean scan (PNG in)", "1200x1600, 1.92MP, 149KB", "1200x1600 JPEG q82, 142KB", "137 → 137"),
        ("dark-bg photo (JPG in)", "1700x2100, 3.57MP, 539KB", "1234x1628 JPEG q82, 339KB", "180 → 139"),
    ]:
        rows.append([C(r[0]), C(r[1]), C(r[2]), C(r[3])])
    story.append(Table(rows, colWidths=[38 * mm, 47 * mm, 50 * mm, 40 * mm], style=TSTYLE))
    story.append(Spacer(1, 2))
    for b in [
        "<b>2000px cap (~171 DPI max).</b> Longest side is clamped to 2000px — a 12MP phone photo keeps a "
        "quarter of its pixels. Print/archival standard is 300 DPI.",
        "<b>Double JPEG compression.</b> JPG uploads are decoded and re-encoded; PNG/WebP uploads are "
        "transcoded into lossy JPEG. Ringing artifacts appear around letters and re-runs compound them.",
        "<b>CLAHE contrast is always on.</b> It evens lighting but boosts sensor noise and can bleach light "
        "stamps, pencil notes, and colored seals. No per-image decision is made.",
        "<b>Three resampling passes.</b> Perspective warp → deskew rotate → final resize. Good filters "
        "(cubic-class), but interpolating three times still blurs fine print, MRZ lines, and micro-text.",
    ]:
        story.append(P("• " + b))

    story.append(Paragraph("2. What each step does — thresholds with worked examples", H1))
    story.append(P("One real dark-background photo through the pipeline (numbers quoted from its log):"))
    story.append(Paragraph("Step 1–2 · Load + find the page", H2))
    story.append(P("<b>Thresholds:</b> file ≤10MB, short side ≥800px, sharpness (Laplacian variance on a 1000px "
                   "copy) ≥100 — measured <b>sharp 921, blurry 0.4</b>, so blurry uploads are stopped here."))
    story.append(L("detect: page covers 53.6% (warp needs 10–97%, rectangularity ≥0.90, 2nd ≤40%) · "
                   "rectangularity 0.995 · 2nd contour 0.0"))
    story.append(P("<b>Reading it:</b> the paper covers about half the photo — inside the 10–97% window, so a "
                   "warp is allowed. Rectangularity 0.995 means the outline is practically a perfect rectangle "
                   "(needs ≥0.90). Second shape 0.0 means no competing object (over 40% would mean two pages and "
                   "reject the upload). Because all three pass, the next step may cut:"))
    story.append(Paragraph("Step 3 · Crop — background removed", H2))
    story.append(L("crop: 4-corner perspective warp (INTER_CUBIC, rectangularity 0.995 ≥ 0.90) · 1% edge shave + "
                   "2% padding · background removed, only the document kept"))
    story.append(P("<b>Reading it:</b> four corners were found on a rectangle-like shape, so the page is "
                   "stretched back into a straight rectangle and everything outside it (dark table) is deleted. "
                   "A 1% shave removes thin background slivers, then a 2% white margin is added so edge stamps "
                   "survive. Photo went 1700x2100 → document-only 1234x1628. "
                   "Counter-example — a full-frame scan covers 99.0% (outside 10–97%), so no warp is attempted: "
                   "“No clean page edge → whitespace_trim: trimming white margins instead of warping”."))
    story.append(Paragraph("Step 4 · Orientation — EXIF proposes, OCR validates", H2))
    story.append(P("The phone's EXIF tag is applied first, then the page shape (portrait vs landscape "
                   "expectation). Only if direction is still unclear does Tesseract OSD read the text — "
                   "accepted at confidence ≥2.0, below that the image is left alone and flagged, never guessed:"))
    story.append(L("sideways photo, FALLBACK: OCR text read (confidence 5.58) → rotated 270° (method osd_fallback)"))
    story.append(L("upside-down photo, FALLBACK: OCR text read (confidence 6.84) → rotated 180° (method osd_fallback)"))
    story.append(P("Without tesseract installed the log says so explicitly "
                   "(“tesseract NOT installed — install it or rotations stay as-is”) instead of failing silently."))
    story.append(Paragraph("Step 5 · Alignment — original tilt vs processed tilt", H2))
    story.append(P("Text-line angle is measured (Hough lines); tilt ≥0.5° is counter-rotated (limit 45°), then "
                   "the tilt is <b>measured again on the result</b>, so the log proves the fix:"))
    story.append(L("align: Original tilt 6.998° (fix at ≥ 0.5°, limit 45°) → rotated 6.998° · processed tilt 0.0°"))
    story.append(P("Level pages log “original tilt 0.0°, processed tilt 0.0° · skipping”, and the canvas is "
                   "re-squared with uniform padding so borders never stay rotated."))
    story.append(Paragraph("Steps 6–7 · Enhance + compose", H2))
    story.append(P("CLAHE contrast, then longest side →2000px (never enlarged) and JPEG q82 for the A4 page."))

    story.append(Paragraph("3. Risks for sensitive and multilingual documents", H1))
    rows = [[Paragraph("<b>risk</b>", CELLH), Paragraph("<b>impact</b>", CELLH),
             Paragraph("<b>status in this PoC</b>", CELLH)]]
    for r in [
        ("ID docs on server disk (raw + cache + derivatives + logs), never auto-purged",
         "Breach exposure; retention liability", "No expiry, no access log, no purge"),
        ("Silent alteration (edge stamps clipped, faint signatures bleached) becomes the record",
         "Evidentiary integrity", "2% padding heuristic only; no diff vs raw"),
        ("OSD orientation is Latin-tuned; Devanagari orients worse, lower confidence",
         "Sideways Nepali docs abstain or mis-rotate", "Needs tesseract-ocr-nep; untested"),
        ("Mixed-script pages (English + Nepali) worst for OSD and text extraction",
         "Garbage text layer, wrong rotation", "No per-language quality signal"),
        ("WARN means <i>something</i> happened, not that text stayed legible",
         "False confidence in bad output", "Human review step missing"),
    ]:
        rows.append([C(r[0]), C(r[1]), C(r[2])])
    story.append(Table(rows, colWidths=[62 * mm, 55 * mm, 58 * mm], style=TSTYLE))

    story.append(Paragraph("4. What to improve (in priority order)", H1))
    for b in [
        "<b>Keep PNG/lossless through to the PDF</b> (or JPEG q90+ and a 300-DPI cap option) — the single "
        "biggest quality lever; transcoding every upload to q82 JPEG is today's main loss.",
        "<b>Human review gate:</b> original-vs-processed comparison already exists on the run page — require "
        "an explicit approve per image before the PDF is sealed.",
        "<b>Retention + access control:</b> auto-purge raws/caches/logs after N days, presigned URLs with "
        "expiry, access logging for ID documents.",
        "<b>Per-image enhance decision:</b> skip CLAHE when the page is already even (measure before/after "
        "sharpness); never bleach stamps by default.",
        "<b>Real-photo calibration set:</b> 100+ labeled phone photos to re-check area/rectangularity/blur/OSD "
        "gates — synthetic values drift on wood grain, glare, and curl.",
        "<b>Multilingual OSD/OCR:</b> install and test tesseract-ocr-nep; add a per-script confidence readout.",
        "<b>Never silently alter records:</b> always archive the untouched original next to the PDF.",
    ]:
        story.append(P("• " + b))

    story.append(Paragraph("5. Recommendation — ask the user for a finished PDF", H1))
    story.append(P("Server-side rebuilding is a lossy, threshold-heavy guess about someone else's document. "
                   "A user-supplied PDF has zero quality loss, zero tuning burden, is language-independent, "
                   "and what the applicant signed is what gets stored. Suggested shape: PDF upload as the "
                   "primary path — validate (page count, size, text layer, first-page thumbnail + blur check) — "
                   "and keep photo→PDF only as an assisted fallback ('no scanner? photograph each page; "
                   "quality not guaranteed')."))
    story.append(Paragraph("Appendix — reference thresholds", H1))
    story.append(Paragraph("Warp needs area 10–97% + rectangularity ≥0.90 + 2nd contour ≤40% (edge retry Canny "
                           "20/60 on failure); deskew at tilt ≥0.5°, limit 45°; OSD accepted at confidence ≥2.0 "
                           "on a 1200px copy; blur ≥100 on 1000px copy; compose longest side 2000px, JPEG q82.",
                           SMALL))
    doc.build(story)
    print("wrote", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    build()
