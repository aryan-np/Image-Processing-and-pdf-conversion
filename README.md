# Image Processing and PDF Conversion (SlotLab)

Upload one image per **slot** (validated at upload time), then **Generate PDF**
builds one formatted A4 PDF from all slot images in a background job.
Slot count is dynamic (➕ add one / add N at once / remove / reorder), so load
can be varied; every processed image is logged (DB rows + `logs/pipeline.jsonl`).

## What it does

- **Sessions & slots** — each session is a document job: slots → validated uploads → one A4 PDF (`/`, `/s/<id>/`, `/runs/<id>/`).
- **Upload validation** — extension, MIME, size, resolution, blur, page-detect checks; rejected files show the reason on the slot.
- **Image pipeline** (Django-free, in `pipeline/`): `load → detect → crop/deskew → orientation → align → enhance → compose`
  - EXIF transpose, page-quad perspective warp (keystone fix), 0/90/180/270° orientation (aspect heuristic + optional Tesseract OSD),
  - fine **skew alignment** (tilt ≥ 0.5° is deskewed, limit 45°; border re-squared + uniform 2% padding so nothing stays rotated),
  - CLAHE contrast enhance, print-size JPEG compose.
- **Document-only crop** — photos on dark/light surfaces get the background warped out (4-corner perspective warp on high-confidence quads,
  page area 10–97% of frame, rectangularity ≥ 0.90 (edge retry via Canny 20/60 when brightness split fails); white-on-white / full-bleed falls back to whitespace trim; a 2nd contour above ~40% rejects the upload as "two objects").
- **Live processing log** — while a run is `PROCESSING` the session page streams a pretty per-photo, per-step log
  (`▸ CITI FRONT` → `detect`/`crop`/`align`… pills, each with its millisecond time), every line stating the threshold/decision,
  e.g. `Original tilt 6.9° (fix at ≥ 0.5°, limit 45°) → rotated 6.9° · processed tilt 0.0°`; the same steps are saved per image and shown on its detail page.
- **OCR modes** — `OFF` (never) · `FALLBACK` (OSD only when orientation is ambiguous) · `ALWAYS` (OSD + full text). Works with tesseract missing (graceful `OFF`).
- **Three actions per session** — **Process Images** (timings only, no PDF), **Create PDF** (builds from the last process — fast, fails with "process first" if slots changed), **Process + Create PDF**.
- **Test-image tools** — per-type % mix + seed fill, plus a **live preview** of the synthetic photo before filling (`GET /api/preview-synthetic/`).
- **Analysis & exports** — `/analysis/` stage breakdown, p50/p95, scaling chart, CSV/JSON export; `/ocr/`, `/settings/` config pages.

## Startup — Python

Requirements: Python 3.12, `libmagic1`, and optionally `tesseract-ocr tesseract-ocr-eng`
(Ubuntu/Debian: `sudo apt-get install -y python3 python3-venv libmagic1 tesseract-ocr tesseract-ocr-eng`).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional, defaults work
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
# or: make install migrate run
```

Open http://localhost:8000 — Sessions → Open → add slots → upload/fill → Process + Create PDF.

## Startup — Docker

```bash
cp .env.example .env   # optional, defaults work
docker compose up --build
# or plain docker:
docker build -t slotlab .
docker run --rm -p 8000:8000 \
  -v slotlab-data:/data -v ./media:/app/media -v ./logs:/app/logs \
  -e SQLITE_PATH=/data/db.sqlite3 slotlab
```

Open http://localhost:8000. The image ships with tesseract, so OCR modes work out of the box.
SQLite lives in the `slotlab-data` volume (`SQLITE_PATH`), uploads in `./media`, pipeline logs in `./logs`.

## Env vars (see `.env.example`)

| var | default | meaning |
|---|---|---|
| `GEN_BACKEND` | `thread` | `thread` (bounded pool) \| `celery` (optional Redis worker) \| `sync` (deterministic tests) |
| `GEN_WORKERS` | `2` | pool size |
| `GEN_TIMELIMIT_SECONDS` | `120` | hard limit per generation |
| `SQLITE_PATH` | `db.sqlite3` | sqlite file (docker compose sets `/data/db.sqlite3`) |
| `UPLOAD_MAX_MB` | `10` | size cap |
| `UPLOAD_MIN_SHORT_SIDE` | `800` | min resolution |
| `UPLOAD_MAX_MEGAPIXELS` | `50` | pixel cap |
| `UPLOAD_BLUR_THRESHOLD` | `100.0` | Laplacian-variance floor |
| `UPLOAD_ALLOW_WEBP` / `UPLOAD_ALLOW_HEIC` | `true`/`false` | extension toggles |
| `ENHANCE_ENABLED` | `true` | CLAHE toggle |
| `OCR_LANGUAGES` | `eng` | e.g. `eng+nep` if installed |
| `OCR_FALLBACK_ENABLED` | `true` | master toggle for FALLBACK OCR: `false` makes FALLBACK behave like OFF (no OCR ever); ALWAYS is unaffected |

Celery (optional): `pip install celery redis`, `redis-server &`,
`celery -A slotlab.celery_app worker -l info`, then `GEN_BACKEND=celery`.

## Load test / benchmark

```bash
GEN_BACKEND=sync python manage.py loadtest --slots 50 --runs 2 --ocr off --mix clean_scan=70,photo_dark_bg=20,sideways_90=10 --seed 1
GEN_BACKEND=sync python manage.py loadtest --sweep 11,25,50,100,200 --runs 1 --ocr off
GEN_BACKEND=sync python manage.py loadtest --slots 11 --runs 1 --ocr always --process-only
```

Report: stdout table + `logs/loadtest_report.json`.

## Tests

```bash
GEN_BACKEND=sync python -m pytest tests/ -v
# or: make test
```

OCR-dependent assertions skip when `tesseract` is absent.

## Layout

- `slotlab/` settings/urls/wsgi/celery_app
- `core/` models, validation, services (generation orchestration), views/API, templates, presets, storage
- `pipeline/` Django-free stages: `load/detect/crop/orient/align/enhance_compose_merge/ocr/runner/synth`
- `Dockerfile`, `docker-compose.yml`, `core/management/commands/loadtest.py`, `tests/`

## Porting `pipeline/` to the real project

`pipeline/` has no Django imports: copy the package, call `process_image()`
(optionally with `log_cb=...` for the live step feed) from a DRF/Celery task,
swap `core/storage.py` for an S3/MinIO implementation (stub in
`core/storage_s3.py`), persist the returned metrics dicts into your own
`*_media_path/*_current_url/*_url_expiry_time` + run models. Settings snapshot
dict is already JSON-serializable for reproducibility.
