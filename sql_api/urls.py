"""Routes for /api/sql/. Same endpoints as /api/mongo/, backed by PostgreSQL."""
from core.api import backend_urlpatterns

from . import repository

app_name = "sql_api"

urlpatterns = backend_urlpatterns(repository, backend="postgresql")
