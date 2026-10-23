"""Top-level URL routes.

/                 home page with database status (core)
/api/             list of every endpoint (core)
/api/sql/...      the API served from PostgreSQL (sql_api)
/api/mongo/...    the same API served from MongoDB (mongo_api)
/compare/...      benchmark dashboard, query inspector, data model view (comparison)
/admin/           Django admin (PostgreSQL tables only)
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("api/sql/", include("sql_api.urls")),        # Member 2
    path("api/mongo/", include("mongo_api.urls")),    # Member 3
    path("compare/", include("comparison.urls")),     # Member 4
]
