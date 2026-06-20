from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("", views.audit_list, name="list"),
    path("<int:event_id>/", views.audit_detail, name="detail"),
]
