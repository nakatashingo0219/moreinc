"""保管場所とサンプルの20品目を登録する。

  python manage.py setup_shop                # 保管場所＋サンプル20品目
  python manage.py setup_shop --demo-users   # 動作確認用のユーザーも作る（開発環境のみ）
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from inventory.models import Location, Product

LOCATIONS = [
    ("店頭", True),
    ("フラワーキーパー（冷蔵ケース）", False),
    ("バックヤード", False),
    ("資材棚", False),
    ("倉庫", False),
]

# 要件定義書「付録A」のサンプル（仮の値）
PRODUCTS = [
    ("P001", "パキラ 3号", "ぱきら", "観葉植物", "鉢", "店頭", 5),
    ("P002", "パキラ 6号", "ぱきら", "観葉植物", "鉢", "店頭", 2),
    ("P003", "ガジュマル 4号", "がじゅまる", "観葉植物", "鉢", "店頭", 3),
    ("P004", "モンステラ 6号", "もんすてら", "観葉植物", "鉢", "店頭", 2),
    ("P005", "サンスベリア 5号", "さんすべりあ", "観葉植物", "鉢", "店頭", 2),
    ("P006", "ポトス 3.5号", "ぽとす", "観葉植物", "鉢", "店頭", 5),
    ("P007", "ドラセナ（幸福の木） 7号", "どらせな こうふくのき", "観葉植物", "鉢", "バックヤード", 1),
    ("P008", "エバーフレッシュ 6号", "えばーふれっしゅ", "観葉植物", "鉢", "店頭", 1),
    ("P009", "フィカス・ウンベラータ 7号", "ふぃかす うんべらーた", "観葉植物", "鉢", "バックヤード", 1),
    ("P010", "オリーブ 6号", "おりーぶ", "観葉植物", "鉢", "店頭", 2),
    ("P011", "多肉植物 2.5号", "たにくしょくぶつ", "鉢物", "鉢", "店頭", 10),
    ("P012", "胡蝶蘭 3本立ち", "こちょうらん", "鉢物", "鉢", "バックヤード", 1),
    ("P013", "シクラメン 5号", "しくらめん", "鉢物（季節）", "鉢", "店頭", 5),
    ("P014", "ポインセチア 5号", "ぽいんせちあ", "鉢物（季節）", "鉢", "店頭", 5),
    ("P015", "プリザーブドフラワー（ドーム型）", "ぷりざーぶどふらわー", "プリザーブド・ドライ", "個", "店頭", 3),
    ("P016", "鉢カバー 6号用", "はちかばー", "資材", "個", "資材棚", 5),
    ("P017", "鉢皿 6号用", "はちざら", "資材", "個", "資材棚", 10),
    ("P018", "ガラス花器（中）", "がらすかき", "資材", "個", "資材棚", 3),
    ("P019", "手提げ袋（中）", "てさげぶくろ", "資材", "枚", "資材棚", 30),
    ("P020", "観葉植物用の土 5L", "かんようしょくぶつようのつち", "資材", "袋", "倉庫", 3),
]

SEASON = {"P013": "クリスマス,年末", "P014": "クリスマス"}


class Command(BaseCommand):
    help = "保管場所とサンプルの20品目を登録します（すでにあるものは変更しません）。"

    def add_arguments(self, parser):
        parser.add_argument("--no-products", action="store_true", help="保管場所だけ登録する")
        parser.add_argument("--demo-users", action="store_true", help="動作確認用ユーザー tencho / staff を作る（開発環境のみ）")

    def handle(self, *args, **opts):
        if opts["verbosity"] == 0:
            self.stdout.write = lambda *a, **k: None
        locs = {}
        for i, (name, default) in enumerate(LOCATIONS, start=1):
            loc, created = Location.objects.get_or_create(name=name, defaults={"sort_order": i * 10, "is_default": default})
            locs[name] = loc
            if created:
                self.stdout.write(f"保管場所を登録：{name}")
        if not Location.objects.filter(is_default=True).exists():
            first = Location.objects.first()
            first.is_default = True
            first.save()

        if not opts["no_products"]:
            n = 0
            for code, name, kana, cat, unit, loc, rp in PRODUCTS:
                _, created = Product.objects.get_or_create(
                    code=code,
                    defaults=dict(
                        name=name, kana=kana, category=cat, unit=unit, default_location=locs.get(loc),
                        reorder_point=rp, season_tags=SEASON.get(code, ""),
                    ),
                )
                n += created
            self.stdout.write(f"サンプル商品を {n}件 登録しました。")

        if opts["demo_users"]:
            if not settings.DEBUG:
                raise CommandError("--demo-users は開発環境（DJANGO_DEBUG=1）でだけ使えます。")
            if not User.objects.filter(username="tencho").exists():
                User.objects.create_user("tencho", password="demo1234", is_staff=True, is_superuser=True)
            if not User.objects.filter(username="staff").exists():
                User.objects.create_user("staff", password="demo1234")
            self.stdout.write("動作確認用ユーザー：tencho（店長）/ staff（スタッフ）、パスワード demo1234")
