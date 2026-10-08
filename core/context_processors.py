import shutil
from django.conf import settings

def slotlab_env(request):
    try:
        import django, PIL, cv2, numpy
        from pipeline.ocr import tesseract_version
        ver = {"py": __import__("sys").version.split()[0], "django": django.__version__,
               "pillow": PIL.__version__, "cv2": cv2.__version__, "numpy": numpy.__version__,
               "tesseract": tesseract_version() or ("missing" if not settings.TESSERACT_AVAILABLE else "?"),
               "ocr_available": settings.TESSERACT_AVAILABLE,
               "ocr_fallback": getattr(settings, "OCR_FALLBACK_ENABLED", True),
               "ocr_langs": getattr(settings, "OCR_LANGUAGES", "eng"),
               "backend": settings.GEN_BACKEND, "workers": settings.GEN_WORKERS}
    except Exception as e:
        ver = {"error": str(e)}
    return {"slotlab": ver}
