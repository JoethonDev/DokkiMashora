from django.urls import path

from . import views

app_name = "deployment"

urlpatterns = [
    path("", views.health_view, name="health"),
]
