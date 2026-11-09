from django import template

register = template.Library()


@register.filter
def undo_id(tags):
    for t in (tags or "").split():
        if t.startswith("undo:"):
            return t[5:]
    return ""


@register.filter
def signed(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return value
    return f"+{v}" if v > 0 else str(v)


@register.filter
def level(tags):
    for t in (tags or "").split():
        if t in ("success", "error", "info", "warning"):
            return t
    return "info"
