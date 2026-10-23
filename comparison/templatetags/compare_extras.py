from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """{{ row|get_item:"postgresql" }} - dictionary lookup with a variable key."""
    try:
        return mapping[key]
    except (KeyError, TypeError, IndexError):
        return None


@register.filter
def bytes_human(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


@register.filter
def backend_name(key):
    return {"postgresql": "PostgreSQL", "mongodb": "MongoDB"}.get(key, key)
