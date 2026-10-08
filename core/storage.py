"""Tiny storage interface: local FS default."""
import json, os, shutil, time
from pathlib import Path
from django.conf import settings

class Storage:
    def save_slot_file(self, session_id, slot_id, filename, data: bytes) -> str:
        rel = f"sessions/{session_id}/slots/{slot_id}_{int(time.time()*1000)}_{filename}"
        dest = Path(settings.MEDIA_ROOT) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return rel

    def save_final_pdf(self, session_id, run_id, data: bytes) -> str:
        rel = f"sessions/{session_id}/final_{run_id}.pdf"
        dest = Path(settings.MEDIA_ROOT) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return rel

    def read(self, media_path: str) -> bytes:
        return (Path(settings.MEDIA_ROOT) / media_path).read_bytes()

    def delete(self, media_path: str):
        try:
            if media_path:
                (Path(settings.MEDIA_ROOT) / media_path).unlink(missing_ok=True)
        except Exception:
            pass

    def exists(self, media_path: str) -> bool:
        return bool(media_path) and (Path(settings.MEDIA_ROOT) / media_path).exists()

    # ---- processed-image cache (for "Create PDF" as a separate step) ----
    def _cache_dir(self, session_id) -> Path:
        d = Path(settings.MEDIA_ROOT) / "_process_cache" / str(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_process_cache(self, session_id, source_hash, run_id, items) -> None:
        """items: list of dicts {order,label,group,layout,jpeg:bytes}. Overwrites previous cache."""
        d = self._cache_dir(session_id)
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        manifest = {"source_hash": source_hash, "run_id": run_id, "items": []}
        for it in items:
            fname = f"{it['order']:04d}.jpg"
            (d / fname).write_bytes(it["jpeg"])
            manifest["items"].append({k: it[k] for k in ("order", "label", "group", "layout")} | {"file": fname})
        (d / "manifest.json").write_text(json.dumps(manifest))

    def load_process_cache(self, session_id):
        """Returns (manifest_dict, dir) or (None, None). Caller checks source_hash."""
        d = Path(settings.MEDIA_ROOT) / "_process_cache" / str(session_id)
        mf = d / "manifest.json"
        if not mf.exists():
            return None, None
        try:
            return json.loads(mf.read_text()), d
        except Exception:
            return None, None

storage = Storage()
