from django.conf import settings


def nav(request):
    ctx = {"APP_NAME": settings.APP_NAME, "COMPANY_NAME": settings.COMPANY_NAME}
    if not request.user.is_authenticated:
        return ctx
    from . import services

    ctx.update(
        nav_unsent=services.unsent_count(),
        nav_pending_upload=services.pending_upload_sync(),
    )
    return ctx
