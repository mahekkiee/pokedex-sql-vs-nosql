from django.urls import path

from . import views

app_name = "comparison"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("queries/", views.queries, name="queries"),
    path("model/", views.data_model, name="model"),
]
