def nav(request):
    if not request.user.is_authenticated:
        return {}
    from . import services

    return {
        "nav_unsent": services.unsent_count(),
        "nav_pending_upload": services.pending_upload_sync(),
    }
