"""SlotLab Django settings."""
import os, shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def env(name, default=None):
    return os.environ.get(name, default)

def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None: return default
    return v.strip().lower() in ("1", "true", "yes", "on")

def env_float(name, default):
    try: return float(os.environ.get(name, default))
    except ValueError: return default

def env_int(name, default):
    try: return int(os.environ.get(name, default))
    except ValueError: return default

SECRET_KEY = env("SECRET_KEY", "django-insecure-slotlab-dev-key-change-me")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "slotlab.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "core" / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "core.context_processors.slotlab_env",
    ]},
}]
WSGI_APPLICATION = "slotlab.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                          # override in docker via SQLITE_PATH=/data/db.sqlite3
                          "NAME": env("SQLITE_PATH", BASE_DIR / "db.sqlite3")}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "core" / "static"]

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"
os.makedirs(MEDIA_ROOT, exist_ok=True)
LOG_DIR = BASE_DIR / "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# ---- SlotLab knobs ----
GEN_BACKEND = env("GEN_BACKEND", "thread")          # thread|celery|sync
GEN_WORKERS = env_int("GEN_WORKERS", 2)
GEN_TIMELIMIT_SECONDS = env_int("GEN_TIMELIMIT_SECONDS", 120)

UPLOAD_MAX_MB = env_float("UPLOAD_MAX_MB", 10)
UPLOAD_MIN_SHORT_SIDE = env_int("UPLOAD_MIN_SHORT_SIDE", 800)
UPLOAD_MAX_MEGAPIXELS = env_float("UPLOAD_MAX_MEGAPIXELS", 50)
UPLOAD_BLUR_THRESHOLD = env_float("UPLOAD_BLUR_THRESHOLD", 100.0)
UPLOAD_ALLOW_WEBP = env_bool("UPLOAD_ALLOW_WEBP", True)
UPLOAD_ALLOW_HEIC = env_bool("UPLOAD_ALLOW_HEIC", False)
ENHANCE_ENABLED = env_bool("ENHANCE_ENABLED", True)
OCR_LANGUAGES = env("OCR_LANGUAGES", "eng")
# Master kill-switch for FALLBACK OCR: when false, ocr_mode=FALLBACK behaves
# like OFF (orientation purely heuristic, zero OCR rows). ALWAYS is unaffected.
OCR_FALLBACK_ENABLED = env_bool("OCR_FALLBACK_ENABLED", True)

from PIL import Image
try:
    Image.MAX_IMAGE_PIXELS = int(UPLOAD_MAX_MEGAPIXELS * 1_000_000 * 1.5)
except Exception:
    pass

TESSERACT_AVAILABLE = shutil.which("tesseract") is not None

LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
