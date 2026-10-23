"""Routes for /api/mongo/. Same endpoints as /api/sql/, backed by MongoDB."""
from core.api import backend_urlpatterns

from . import repository

app_name = "mongo_api"

urlpatterns = backend_urlpatterns(repository, backend="mongodb")
