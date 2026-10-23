from datetime import datetime, timezone


def iso(value: datetime) -> str:
    """Format a datetime the same way for both backends.

    PostgreSQL stores microseconds, MongoDB stores milliseconds, so we round
    both to milliseconds and always print UTC with a trailing "Z".
    """
    if value.tzinfo is None:  # PyMongo can return naive datetimes that are already UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
