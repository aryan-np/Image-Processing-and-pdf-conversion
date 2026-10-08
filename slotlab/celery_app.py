"""Optional Celery backend. Requires redis + `pip install celery redis`.

Run:
    redis-server &
    celery -A slotlab.celery_app worker -l info
    GEN_BACKEND=celery python manage.py runserver

If celery/redis are unavailable, use GEN_BACKEND=thread (default) or sync.
"""
import os
try:
    from celery import Celery
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "slotlab.settings")
    app = Celery("slotlab", broker=os.environ.get("CELERY_BROKER", "redis://localhost:6379/0"))
    app.config_from_object("django.conf:settings", namespace="CELERY")

    @app.task(bind=True, name="slotlab.generate")
    def generate_task(self, session_id):
        import django
        django.setup()
        from core.services import run_generation_sync
        return run_generation_sync(session_id, backend="celery")
except ImportError:  # celery not installed -> import-safe stub
    app = None
    generate_task = None
