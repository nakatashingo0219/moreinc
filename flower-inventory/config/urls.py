from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from django.conf import settings

admin.site.site_header = f"{settings.APP_NAME}｜設定"
admin.site.site_title = f"{settings.APP_NAME}｜{settings.COMPANY_NAME}"
admin.site.index_title = "設定"
admin.site.site_url = "/"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("inventory.urls")),
]
