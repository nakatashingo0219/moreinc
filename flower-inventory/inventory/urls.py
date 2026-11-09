from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("stock/", views.stock_list, name="stock_list"),
    path("products/<int:pk>/", views.product_detail, name="product_detail"),
    path("record/<str:kind>/", views.record_select, name="record_select"),
    path("record/<str:kind>/<int:pk>/", views.record_form, name="record_form"),
    path("movements/<int:pk>/cancel/", views.cancel_movement, name="cancel_movement"),
    path("history/", views.history, name="history"),
    path("stocktake/", views.stocktake_list, name="stocktake_list"),
    path("stocktake/new/", views.stocktake_new, name="stocktake_new"),
    path("stocktake/<int:pk>/", views.stocktake_edit, name="stocktake_edit"),
    path("stocktake/<int:pk>/review/", views.stocktake_review, name="stocktake_review"),
    path("stocktake/<int:pk>/confirm/", views.stocktake_confirm, name="stocktake_confirm"),
    path("stocktake/<int:pk>/csv/", views.stocktake_csv, name="stocktake_csv"),
    path("airregi/", views.airregi_sync, name="airregi_sync"),
    path("airregi/<int:pk>/register/", views.airregi_register, name="airregi_register"),
    path("airregi/<int:pk>/export/", views.airregi_export, name="airregi_export"),
    path("airregi/<int:pk>/uploaded/", views.airregi_uploaded, name="airregi_uploaded"),
    path("products/", views.product_list, name="product_list"),
    path("products/csv/", views.product_csv_template, name="product_csv_template"),
    path("products/csv/import/", views.product_csv_import, name="product_csv_import"),
]
