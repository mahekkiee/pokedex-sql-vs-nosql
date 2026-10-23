"""The API contract shared by both backends.

sql_api and mongo_api expose exactly the same endpoints and return exactly the
same JSON. The views below do not know which database they are talking to:
each app hands in its own `repository` module (the "repository pattern").
So the ONLY code that differs between PostgreSQL and MongoDB lives in
sql_api/repository.py and mongo_api/repository.py, which is what we compare.

Every response carries two headers:
    X-Backend      postgresql | mongodb
    X-DB-Time-ms   time spent inside the repository call (database work)
"""
import time

from django.urls import path, reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    PokemonListQuerySerializer, TeamInputSerializer, TopStatQuerySerializer,
)


class BackendView(APIView):
    repository = None  # module with the data-access functions
    backend = ""       # "postgresql" or "mongodb"

    def run(self, func_name, *args, status_code=status.HTTP_200_OK, **kwargs):
        func = getattr(self.repository, func_name)
        started = time.perf_counter()
        data = func(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response = Response(data, status=status_code)
        response["X-Backend"] = self.backend
        response["X-DB-Time-ms"] = f"{elapsed_ms:.3f}"
        return response

    def get_view_name(self):
        return f"{super().get_view_name()} [{self.backend}]"


class PokemonListView(BackendView):
    """List Pokemon. Filters: ?type=fire&search=char&min_attack=80&ordering=-attack&limit=20&offset=0"""

    def get(self, request):
        query = PokemonListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return self.run("list_pokemon", **query.validated_data)


class PokemonDetailView(BackendView):
    """One Pokemon by PokeAPI id or by name, with types, abilities and stats."""

    def get(self, request, id_or_name):
        return self.run("get_pokemon", id_or_name)


class TypeListView(BackendView):
    """Every type with the number of Pokemon that have it."""

    def get(self, request):
        return self.run("list_types")


class StatsByTypeView(BackendView):
    """Average base stats per type (an aggregation query)."""

    def get(self, request):
        return self.run("stats_by_type")


class TopStatView(BackendView):
    """Top Pokemon for one stat. ?stat=attack&limit=10"""

    def get(self, request):
        query = TopStatQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return self.run("top_by_stat", **query.validated_data)


class TeamListView(BackendView):
    """List teams, or create one: {"name": "Kanto", "members": [{"pokemon": 25, "nickname": "Sparky"}]}"""

    def get(self, request):
        return self.run("list_teams")

    def post(self, request):
        body = TeamInputSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return self.run("create_team", body.validated_data, status_code=status.HTTP_201_CREATED)


class TeamDetailView(BackendView):
    """Read, replace or delete one team. A team references Pokemon by id (a join on read)."""

    def get(self, request, team_id):
        return self.run("get_team", team_id)

    def put(self, request, team_id):
        body = TeamInputSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return self.run("update_team", team_id, body.validated_data)

    def delete(self, request, team_id):
        return self.run("delete_team", team_id, status_code=status.HTTP_204_NO_CONTENT)


ENDPOINTS = (
    # (name, route, view class)
    ("pokemon-list", "pokemon/", PokemonListView),
    ("pokemon-detail", "pokemon/<str:id_or_name>/", PokemonDetailView),
    ("type-list", "types/", TypeListView),
    ("stats-by-type", "stats/by-type/", StatsByTypeView),
    ("stats-top", "stats/top/", TopStatView),
    ("team-list", "teams/", TeamListView),
    ("team-detail", "teams/<str:team_id>/", TeamDetailView),
)


class BackendRootView(BackendView):
    """Entry point of one backend: links to every endpoint it serves."""

    def get(self, request):
        namespace = request.resolver_match.namespace
        links = {}
        for name, route, _view in ENDPOINTS:
            if "<" in route:
                continue
            links[name] = request.build_absolute_uri(reverse(f"{namespace}:{name}"))
        links["pokemon-detail (example)"] = request.build_absolute_uri(
            reverse(f"{namespace}:pokemon-detail", args=["pikachu"])
        )
        return Response({"backend": self.backend, "endpoints": links})


def backend_urlpatterns(repository, backend):
    """Build the same URL list for any backend's repository module."""
    patterns = [
        path("", BackendRootView.as_view(repository=repository, backend=backend), name="root")
    ]
    for name, route, view_class in ENDPOINTS:
        view = view_class.as_view(repository=repository, backend=backend)
        patterns.append(path(route, view, name=name))
    return patterns
