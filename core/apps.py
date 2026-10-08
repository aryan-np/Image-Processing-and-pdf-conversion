from django.apps import AppConfig

class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Cleanup stuck PROCESSING rows after migrations (no DB access at import).
        try:
            from django.db.models.signals import post_migrate

            def _cleanup(**kwargs):
                try:
                    from .models import Session
                    Session.objects.filter(final_status="PROCESSING").update(
                        final_status="FAILED",
                        final_error="stuck PROCESSING cleaned on startup")
                except Exception:
                    pass
            post_migrate.connect(_cleanup, dispatch_uid="slotlab_cleanup")
        except Exception:
            pass
