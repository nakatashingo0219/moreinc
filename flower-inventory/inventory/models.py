from django.conf import settings
from django.db import models
from django.db.models import Q, Sum


class Location(models.Model):
    name = models.CharField("名前", max_length=50, unique=True)
    sort_order = models.PositiveIntegerField("並び順", default=0)
    is_default = models.BooleanField(
        "基本の場所",
        default=False,
        help_text="入荷の初期値、Airレジで売れた分を減らす場所。1か所だけ選ぶ。",
    )

    class Meta:
        verbose_name = verbose_name_plural = "保管場所"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Location.objects.exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def default(cls):
        return cls.objects.filter(is_default=True).first() or cls.objects.first()


class Product(models.Model):
    code = models.CharField("商品コード", max_length=40, unique=True)
    name = models.CharField("商品名", max_length=100)
    kana = models.CharField("ふりがな", max_length=100, blank=True, help_text="検索用（任意）")
    category = models.CharField("カテゴリ", max_length=50, blank=True)
    unit = models.CharField("単位", max_length=10, default="個")
    season_tags = models.CharField("季節タグ", max_length=100, blank=True, help_text="例：クリスマス,母の日")
    shelf_life_days = models.PositiveIntegerField(
        "日持ちの目安（日）", null=True, blank=True, help_text="切り花など。空欄なら通知しない"
    )
    reorder_point = models.PositiveIntegerField("発注点", default=0, help_text="在庫がこの数以下になったら通知（0なら通知しない）")
    default_location = models.ForeignKey(
        Location, verbose_name="いつもの保管場所", null=True, blank=True, on_delete=models.SET_NULL
    )
    is_active = models.BooleanField("管理する", default=True, help_text="オフにすると画面に表示しない（履歴は残る）")
    memo = models.TextField("メモ", blank=True)

    airregi_product_id = models.CharField("Airレジ商品ID", max_length=40, blank=True)
    airregi_variation_id = models.CharField("AirレジバリエーションID", max_length=40, blank=True)
    airregi_baseline = models.IntegerField(
        "Airレジ側の確定在庫数",
        null=True,
        blank=True,
        help_text="前回の同期で確定した、Airレジ上の在庫数。システムが自動で更新する。",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = verbose_name_plural = "商品"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["airregi_product_id", "airregi_variation_id"],
                condition=~Q(airregi_product_id=""),
                name="uniq_airregi_key",
            )
        ]

    def __str__(self):
        return self.name

    @property
    def airregi_key(self):
        return (self.airregi_product_id, self.airregi_variation_id)

    @property
    def is_linked(self):
        return bool(self.airregi_product_id)

    def total_qty(self):
        return self.stocks.aggregate(t=Sum("qty"))["t"] or 0

    @property
    def is_low(self):
        return self.reorder_point > 0 and self.total_qty() <= self.reorder_point


class Stock(models.Model):
    product = models.ForeignKey(Product, related_name="stocks", on_delete=models.CASCADE)
    location = models.ForeignKey(Location, related_name="stocks", on_delete=models.PROTECT)
    qty = models.IntegerField("数量", default=0)

    class Meta:
        verbose_name = verbose_name_plural = "在庫"
        constraints = [models.UniqueConstraint(fields=["product", "location"], name="uniq_stock")]
        ordering = ["location__sort_order", "location_id"]

    def __str__(self):
        return f"{self.product} @ {self.location}: {self.qty}"


class Movement(models.Model):
    RECEIVE = "receive"
    WASTE = "waste"
    MOVE = "move"
    ADJUST = "adjust"
    OFFSALE = "offsale"
    STOCKTAKE = "stocktake"
    AIRREGI_SALE = "airregi_sale"
    AIRREGI_ADJUST = "airregi_adjust"
    INITIAL = "initial"

    KIND_CHOICES = [
        (RECEIVE, "入荷"),
        (WASTE, "廃棄"),
        (MOVE, "移動"),
        (ADJUST, "数の修正"),
        (OFFSALE, "Airレジを通さない販売"),
        (STOCKTAKE, "棚卸し"),
        (AIRREGI_SALE, "販売（Airレジ）"),
        (AIRREGI_ADJUST, "Airレジ側の修正"),
        (INITIAL, "初期登録"),
    ]

    # このシステムの記録 → Airレジの「更新種別」。ないものはAirレジへ送らない
    AIRREGI_KIND = {
        RECEIVE: "入荷",
        WASTE: "破損・不良品",
        ADJUST: "その他",
        OFFSALE: "販売",
        STOCKTAKE: "棚卸し・在庫確認",
    }

    WASTE_REASONS = ["しおれ", "傷み", "破損", "その他"]

    created_at = models.DateTimeField("日時", auto_now_add=True)
    product = models.ForeignKey(Product, related_name="movements", on_delete=models.PROTECT)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField("種類", max_length=20, choices=KIND_CHOICES)
    qty = models.IntegerField("増減")
    reason = models.CharField("理由", max_length=50, blank=True)
    note = models.CharField("メモ", max_length=200, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    group = models.CharField(max_length=36, blank=True, help_text="一緒に記録した行（移動の出と入りなど）")
    cancel_of = models.ForeignKey(
        "self", null=True, blank=True, related_name="cancelled_by", on_delete=models.PROTECT
    )

    needs_send = models.BooleanField("Airレジへ送る", default=False)
    export_sync = models.ForeignKey(
        "AirregiSync", null=True, blank=True, related_name="exported_movements", on_delete=models.SET_NULL
    )
    sent = models.BooleanField("Airレジへ送信済み", default=False)

    class Meta:
        verbose_name = verbose_name_plural = "入出庫の記録"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.product} {self.qty:+d}"


class AirregiSync(models.Model):
    IMPORTED = "imported"
    EXPORTED = "exported"
    UPLOADED = "uploaded"
    DISCARDED = "discarded"
    STATUS_CHOICES = [
        (IMPORTED, "取り込み済み"),
        (EXPORTED, "Airレジ用ファイル作成済み（アップロード待ち）"),
        (UPLOADED, "完了"),
        (DISCARDED, "取り消し"),
    ]

    imported_at = models.DateTimeField("取り込み日時", auto_now_add=True)
    filename = models.CharField("ファイル名", max_length=255)
    raw = models.BinaryField("元のファイル")
    status = models.CharField("状態", max_length=20, choices=STATUS_CHOICES, default=IMPORTED)
    exported_at = models.DateTimeField("ファイル作成日時", null=True, blank=True)
    uploaded_at = models.DateTimeField("アップロード確認日時", null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    summary = models.JSONField("結果", default=dict, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "Airレジ同期"
        ordering = ["-imported_at", "-id"]

    def __str__(self):
        return f"{self.imported_at:%Y-%m-%d %H:%M} {self.filename}"


class AirregiSyncLine(models.Model):
    """取り込んだときのAirレジ在庫数と、送った差分。"""

    sync = models.ForeignKey(AirregiSync, related_name="lines", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    airregi_qty = models.IntegerField("取り込み時のAirレジ在庫数", null=True)
    change_from_airregi = models.IntegerField("Airレジ側の変動", default=0)
    export_qty = models.IntegerField("送った増減", null=True, blank=True)
    export_kind = models.CharField(max_length=20, blank=True)
    export_memo = models.CharField(max_length=60, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sync", "product"], name="uniq_sync_product")]


class Stocktake(models.Model):
    DRAFT = "draft"
    DONE = "done"
    STATUS_CHOICES = [(DRAFT, "入力中"), (DONE, "確定")]

    title = models.CharField("名前", max_length=100)
    status = models.CharField("状態", max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = verbose_name_plural = "棚卸し"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class StocktakeLine(models.Model):
    stocktake = models.ForeignKey(Stocktake, related_name="lines", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    counted = models.IntegerField("実際の数", null=True, blank=True)
    system_qty = models.IntegerField("システム上の数（確定時）", null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["stocktake", "product", "location"], name="uniq_stocktake_line")
        ]
