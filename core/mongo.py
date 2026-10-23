"""MongoDB connection helper.

Django's ORM only works with SQL databases, so the MongoDB side of the project
uses the official driver, PyMongo, directly. A MongoClient holds its own
connection pool, so we create ONE client per process and reuse it.

This module also contains a small "command recorder". PyMongo can report
every command it sends to the server; the query inspector page switches the
recorder on for a moment to show exactly what MongoDB received, the same way
Django can show the SQL it sent to PostgreSQL.
"""
import contextvars
import threading
from contextlib import contextmanager

from django.conf import settings
from pymongo import MongoClient, monitoring

# Internal driver chatter we do not want to show on the inspector page.
_IGNORED_COMMANDS = {
    "hello", "ismaster", "isMaster", "ping", "endSessions", "buildInfo",
    "saslStart", "saslContinue", "killCursors",
}
# Driver bookkeeping fields removed from recorded commands.
_NOISE_FIELDS = ("lsid", "$db", "$clusterTime", "$readPreference", "txnNumber")


class CommandRecorder(monitoring.CommandListener):
    """Collects the commands sent to MongoDB while `record()` is active."""

    _bucket = contextvars.ContextVar("mongo_command_bucket", default=None)

    def started(self, event):
        bucket = self._bucket.get()
        if bucket is None or event.command_name in _IGNORED_COMMANDS:
            return
        body = dict(event.command)
        for field in _NOISE_FIELDS:
            body.pop(field, None)
        bucket.append({"command": event.command_name, "body": body})

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass

    @contextmanager
    def record(self):
        bucket = []
        token = self._bucket.set(bucket)
        try:
            yield bucket
        finally:
            self._bucket.reset(token)


recorder = CommandRecorder()

_client = None
_client_lock = threading.Lock()


def get_client() -> MongoClient:
    """Return the shared MongoClient, creating it on first use."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MongoClient(
                    settings.MONGO_URI,
                    serverSelectionTimeoutMS=settings.MONGO_TIMEOUT_MS,
                    tz_aware=True,
                    appname="pokedex-sql-vs-nosql",
                    event_listeners=[recorder],
                )
    return _client


def get_db():
    """Return the project database (name comes from MONGO_DB_NAME)."""
    return get_client()[settings.MONGO_DB_NAME]


def mongo_is_available() -> bool:
    try:
        get_client().admin.command("ping")
        return True
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


def mongo_server_version() -> str:
    try:
        return get_client().server_info().get("version", "unknown")
    except Exception:  # noqa: BLE001
        return "unavailable"
