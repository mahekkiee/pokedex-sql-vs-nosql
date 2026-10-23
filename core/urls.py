from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("api/", views.api_index, name="api-index"),
    path("api/health/", views.health, name="health"),
]
