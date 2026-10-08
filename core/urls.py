from django.urls import path
from . import views_pages, views_api

urlpatterns = [
    path("", views_pages.index, name="index"),
    path("s/<str:sid>/", views_pages.workspace, name="workspace"),
    path("runs/<int:rid>/", views_pages.run_detail, name="run_detail"),
    path("runs/<int:rid>/image/<int:iid>/", views_pages.image_detail, name="image_detail"),
    path("ocr/", views_pages.ocr_list, name="ocr_list"),
    path("ocr/<int:oid>/", views_pages.ocr_detail, name="ocr_detail"),
    path("analysis/", views_pages.analysis, name="analysis"),
    path("analysis/export.<str:fmt>", views_pages.analysis_export, name="analysis_export"),
    path("settings/", views_pages.settings_view, name="settings_view"),
    # API
    path("api/sessions/", views_api.sessions, name="api_sessions"),
    path("api/sessions/<str:sid>/", views_api.session_detail, name="api_session"),
    path("api/sessions/<str:sid>/slots/", views_api.add_slots, name="api_add_slots"),
    path("api/sessions/<str:sid>/upload-bulk/", views_api.upload_bulk, name="api_upload_bulk"),
    path("api/sessions/<str:sid>/fill-synthetic/", views_api.fill_synthetic, name="api_fill"),
    path("api/preview-synthetic/", views_api.preview_synthetic, name="api_preview_synthetic"),
    path("api/sessions/<str:sid>/generate/", views_api.generate, name="api_generate"),
    path("api/sessions/<str:sid>/create-pdf/", views_api.create_pdf, name="api_create_pdf"),
    path("api/slots/<int:slot_id>/", views_api.slot_detail, name="api_slot"),
    path("api/slots/<int:slot_id>/upload/", views_api.slot_upload, name="api_slot_upload"),
    path("api/runs/<int:rid>/", views_api.run_api, name="api_run"),
    path("api/runs/<int:rid>/images/", views_api.run_images, name="api_run_images"),
]
