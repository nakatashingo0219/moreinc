from django.contrib import admin

from .models import AirregiSync, Location, Movement, Product, Stocktake


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_default")
    list_editable = ("sort_order", "is_default")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "unit", "reorder_point", "default_location", "is_active", "airregi_product_id")
    list_filter = ("is_active", "category", "default_location")
    search_fields = ("code", "name", "kana")
    readonly_fields = ("airregi_baseline",)
    fieldsets = (
        (None, {"fields": ("code", "name", "kana", "category", "unit", "default_location", "is_active")}),
        ("通知", {"fields": ("reorder_point", "shelf_life_days", "season_tags")}),
        ("Airレジ連携", {"fields": ("airregi_product_id", "airregi_variation_id", "airregi_baseline")}),
        ("その他", {"fields": ("memo",)}),
    )


@admin.register(Movement)
class MovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "product", "location", "kind", "qty", "reason", "user", "sent")
    list_filter = ("kind", "sent", "location")
    search_fields = ("product__name", "product__code")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(AirregiSync)
class AirregiSyncAdmin(admin.ModelAdmin):
    list_display = ("imported_at", "filename", "status", "exported_at", "uploaded_at")
    exclude = ("raw",)

    def has_add_permission(self, request):
        return False


@admin.register(Stocktake)
class StocktakeAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "created_at", "completed_at")
