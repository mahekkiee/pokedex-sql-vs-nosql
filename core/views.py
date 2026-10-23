"""Home page, health check and API index."""
from django.apps import apps
from django.db import connection
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .mongo import get_db, mongo_is_available, mongo_server_version


def postgres_status():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            version = cursor.fetchone()[0]
        status = {"ok": True, "version": version.split()[0]}
    except Exception as exc:  # noqa: BLE001 - show any connection problem on the page
        return {"ok": False, "error": str(exc).strip().splitlines()[0]}
    if apps.is_installed("sql_api"):
        try:
            from sql_api.models import Pokemon, Team
            status["pokemon"] = Pokemon.objects.count()
            status["teams"] = Team.objects.count()
        except Exception:  # noqa: BLE001 - tables missing: migrations not run yet
            status["pokemon"] = None
            status["hint"] = "Run: python manage.py migrate"
    return status


def mongodb_status():
    if not mongo_is_available():
        return {"ok": False, "error": "MongoDB is not reachable. Is it running?"}
    db = get_db()
    return {
        "ok": True,
        "version": mongo_server_version(),
        "pokemon": db["pokemon"].count_documents({}),
        "teams": db["teams"].count_documents({}),
    }


def home(request):
    context = {
        "postgres": postgres_status(),
        "mongodb": mongodb_status(),
        "has_sql": apps.is_installed("sql_api"),
        "has_mongo": apps.is_installed("mongo_api"),
        "has_comparison": apps.is_installed("comparison"),
    }
    return render(request, "core/home.html", context)


@api_view(["GET"])
def health(request):
    """GET /api/health/ : is each database reachable, and how much data does it hold?"""
    data = {"postgresql": postgres_status(), "mongodb": mongodb_status()}
    ok = data["postgresql"]["ok"] and data["mongodb"]["ok"]
    return Response(data, status=200 if ok else 503)


@api_view(["GET"])
def api_index(request):
    """GET /api/ : where to find each version of the API."""
    links = {"health": request.build_absolute_uri(reverse("core:health"))}
    for key, namespace in (("postgresql", "sql_api"), ("mongodb", "mongo_api")):
        try:
            links[key] = request.build_absolute_uri(reverse(f"{namespace}:root"))
        except NoReverseMatch:  # that backend's URLs are not added yet
            pass
    return Response(links)
