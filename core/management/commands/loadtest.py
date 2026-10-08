import random
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "SlotLab loadtest: create sessions, fill synthetic, generate, report."

    def add_arguments(self, p):
        p.add_argument("--slots", type=int, default=11)
        p.add_argument("--runs", type=int, default=1)
        p.add_argument("--ocr", default="off", choices=["off", "fallback", "always"])
        p.add_argument("--workers", type=int, default=0)
        p.add_argument("--mix", default="clean_scan=70,photo_dark_bg=20,sideways_90=10")
        p.add_argument("--seed", type=int, default=1)
        p.add_argument("--sweep", default="")
        p.add_argument("--process-only", action="store_true")
        p.add_argument("--concurrent-sessions", type=int, default=1)

    def handle(self, *a, **o):
        import os
        os.environ.setdefault("GEN_BACKEND", "sync")
        from django.conf import settings
        settings.GEN_BACKEND = "sync"
        if o["workers"]: settings.GEN_WORKERS = o["workers"]
        from core.models import Session, GenerationRun
        from core.presets import make_slots
        from pipeline.synth import generate, pick_scenario, parse_mix
        from core.validation import validate_upload
        from core.storage import storage
        from core.services import run_generation_sync, run_generation
        from django.utils import timezone
        ocr = {"off": "OFF", "fallback": "FALLBACK", "always": "ALWAYS"}[o["ocr"]]
        sweep = [int(x) for x in o["sweep"].split(",") if x.strip()] if o["sweep"] else [o["slots"]]
        mix = parse_mix(o["mix"])
        all_runs = []
        import concurrent.futures
        for nslots in sweep:
            for r in range(o["runs"]):
                sess_ids = []
                for c in range(o["concurrent_sessions"]):
                    s = Session.objects.create(name=f"loadtest-{nslots}-{r}-{c}", ocr_mode=ocr)
                    make_slots(s, "realistic" if nslots in (11, 12) else str(nslots),
                               count=None if nslots in (11, 12) else nslots)
                    # fill
                    rng = random.Random(o["seed"] + r * 100 + c)
                    for i, slot in enumerate(s.slots.order_by("order")):
                        sc = pick_scenario(mix, rng)
                        data, ext = generate(sc, seed=o["seed"] + i, title=slot.label)
                        ok, err, measured, logs = validate_upload(data, f"{slot.label}.{ext}")
                        if not ok:  # try clean fallback so generation can run
                            data, ext = generate("clean_scan", seed=o["seed"] + i, title=slot.label)
                            ok, err, measured, logs = validate_upload(data, f"{slot.label}.{ext}")
                            if not ok: continue
                        rel = storage.save_slot_file(s.id, slot.id, f"{slot.label}.{ext}", data)
                        slot.media_path = rel; slot.original_filename = f"{slot.label}.{ext}"
                        slot.size_bytes = len(data); slot.width = measured.get("w", 0)
                        slot.height = measured.get("h", 0); slot.uploaded_at = timezone.now()
                        slot.upload_status = "OK"; slot.save()
                    sess_ids.append(s.id)
                # trigger generations concurrently
                with concurrent.futures.ThreadPoolExecutor(max_workers=o["concurrent_sessions"]) as pool:
                    futs = [pool.submit(self._gen_blocking, sid, o["process_only"]) for sid in sess_ids]
                    for f in futs: all_runs.append(f.result())
        # print table
        print(f"{'run':>5} {'slots':>6} {'ocr':>9} {'total':>10} {'images':>10} {'pdf':>10} {'ms/img':>8} {'pages':>6}")
        for rid in all_runs:
            g = GenerationRun.objects.get(pk=rid)
            ms_img = g.total_ms / max(1, g.images_total)
            print(f"{g.id:>5} {g.images_total:>6} {g.ocr_mode:>9} {g.total_ms:>10.0f} {g.images_ms:>10.0f} {g.pdf_ms:>10.0f} {ms_img:>8.1f} {g.pages:>6}")
        # save report
        import json
        rep = [{"run": GenerationRun.objects.get(pk=i).id,
                "slots": GenerationRun.objects.get(pk=i).images_total,
                "total_ms": GenerationRun.objects.get(pk=i).total_ms,
                "images_ms": GenerationRun.objects.get(pk=i).images_ms,
                "pdf_ms": GenerationRun.objects.get(pk=i).pdf_ms,
                "stage_ms": GenerationRun.objects.get(pk=i).stage_ms} for i in all_runs]
        os.makedirs("logs", exist_ok=True)
        with open("logs/loadtest_report.json", "w") as f: json.dump(rep, f, indent=1)
        print("saved logs/loadtest_report.json  (see /analysis/ for charts + CSV/JSON export)")

    def _gen_blocking(self, session_id, process_only):
        from core.services import run_generation
        rid = run_generation(session_id, process_only=process_only)
        # sync backend runs inline; thread backend: wait
        import time
        from core.models import GenerationRun
        for _ in range(600):
            g = GenerationRun.objects.get(pk=rid)
            if g.final_status in ("READY", "FAILED"): return rid
            time.sleep(0.5)
        return rid
