from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase

from inventory import airregi, services
from inventory.models import AirregiSync, Location, Movement, Product, Stock, Stocktake

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "airregi_stock_sample.csv"


def qty(product, location=None):
    if location is None:
        return product.total_qty()
    return Stock.objects.filter(product=product, location=location).values_list("qty", flat=True).first() or 0


def airregi_file(values):
    """{商品ID: 現在在庫数} を差し替えたサンプルファイルを作る。"""
    parsed = airregi.parse(SAMPLE.read_bytes(), "stock.csv")
    for item in parsed.items:
        if item.product_id in values:
            parsed.rows[item.line_index][airregi.COL_CURRENT] = str(values[item.product_id])
    return airregi.build(parsed, {})


def exported_rows(data):
    parsed = airregi.parse(data, "stock.csv")
    return {
        r[airregi.COL_CODE]: (int(r[airregi.COL_UPDATE_QTY]), r[airregi.COL_UPDATE_KIND], r[airregi.COL_UPDATE_MEMO])
        for r in parsed.rows
        if r[airregi.COL_UPDATE_QTY]
    }


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("staff", password="x")
        self.front = Location.objects.create(name="店頭", sort_order=1, is_default=True)
        self.back = Location.objects.create(name="バックヤード", sort_order=2)
        self.products = {}
        for i in range(1, 21):
            code = f"P{i:03d}"
            self.products[code] = Product.objects.create(code=code, name=code, unit="鉢", default_location=self.front)

    def p(self, code):
        return Product.objects.get(code=code)


class MovementTests(Base):
    def test_receive_waste_move_adjust_cancel(self):
        p = self.p("P001")
        services.receive(p, self.front, 10, self.user)
        services.waste(p, self.front, 2, "しおれ", self.user)
        services.move(p, self.front, self.back, 3, self.user)
        self.assertEqual((qty(p, self.front), qty(p, self.back)), (5, 3))
        services.adjust(p, self.back, 1, self.user)
        self.assertEqual(qty(p, self.back), 1)
        self.assertEqual(qty(p), 6)

        mv = Movement.objects.filter(kind=Movement.MOVE).first()
        services.cancel(mv, self.user)
        self.assertEqual((qty(p, self.front), qty(p, self.back)), (8, -2))
        with self.assertRaises(services.InventoryError):
            services.cancel(mv, self.user)

    def test_invalid_quantities(self):
        p = self.p("P001")
        for bad in (0, -1, "abc", None):
            with self.assertRaises(services.InventoryError):
                services.receive(p, self.front, bad)
        with self.assertRaises(services.InventoryError):
            services.move(p, self.front, self.front, 1)

    def test_only_shop_changes_are_sent_to_airregi(self):
        p = self.p("P001")
        services.receive(p, self.front, 1)
        services.move(p, self.front, self.back, 1)
        self.assertEqual(services.unsent_count(), 1)


class AirregiSyncTests(Base):
    def first_import(self):
        return services.import_airregi(SAMPLE.read_bytes(), "stock.csv", self.user)

    def test_first_import_links_by_code_and_starts_from_airregi_qty(self):
        sync = self.first_import()
        p = self.p("P001")
        self.assertEqual(p.airregi_key, ("20001", "0"))
        self.assertEqual(p.airregi_baseline, 6)
        self.assertEqual(qty(p, self.front), 6)
        self.assertEqual(len(sync.summary["unmatched"]), 2)
        self.assertEqual(len(sync.summary["new_links"]), 20)
        # 何も変えていなければ、Airレジに送る行はない
        data = services.export_airregi(sync)
        self.assertEqual(exported_rows(data), {})
        self.assertEqual(data, SAMPLE.read_bytes())

    def test_full_cycle(self):
        sync = self.first_import()
        services.confirm_uploaded(services.export_airregi(sync) and sync)

        p2, p3 = self.p("P002"), self.p("P003")
        services.receive(p2, self.front, 5, self.user)
        services.waste(p3, self.front, 1, "傷み", self.user)
        services.move(self.p("P001"), self.front, self.back, 2, self.user)

        # 2回目: Airレジでは P002 が 2鉢売れていた（3 → 1）
        sync2 = services.import_airregi(airregi_file({"20002": 1}), "stock.csv", self.user)
        self.assertEqual(sync2.summary["changes"], [{"name": "P002", "change": -2, "unit": "鉢"}])
        self.assertEqual(qty(p2), 3 + 5 - 2)
        self.assertTrue(Movement.objects.filter(product=p2, kind=Movement.AIRREGI_SALE, qty=-2).exists())

        rows = exported_rows(services.export_airregi(sync2))
        self.assertEqual(rows["P002"], (5, "入荷", "入荷+5"))
        self.assertEqual(rows["P003"], (-1, "破損・不良品", "廃棄-1"))
        self.assertNotIn("P001", rows)

        # アップロード後の入荷は次回に回る
        services.receive(p2, self.front, 1)
        services.confirm_uploaded(AirregiSync.objects.get(pk=sync2.pk))
        self.assertEqual(self.p("P002").airregi_baseline, 1 + 5)
        self.assertEqual(services.unsent_count(), 1)

        # 3回目: Airレジ側は 6 のまま（売れていない）→ 変動なし、未送信の+1を送る
        sync3 = services.import_airregi(airregi_file({"20002": 6, "20003": 3}), "stock.csv", self.user)
        self.assertEqual(sync3.summary["changes"], [])
        rows = exported_rows(services.export_airregi(sync3))
        self.assertEqual(rows, {"P002": (1, "入荷", "入荷+1")})

    def test_sales_taken_from_default_location_first(self):
        self.first_import()
        p = self.p("P001")  # 店頭 6
        services.confirm_uploaded(services.export_airregi(services.latest_sync()) and services.latest_sync())
        services.move(p, self.front, self.back, 5)  # 店頭1 / バックヤード5
        services.import_airregi(airregi_file({"20001": 3}), "stock.csv")  # 3つ売れた
        self.assertEqual((qty(p, self.front), qty(p, self.back)), (0, 3))

    def test_import_asks_whether_previous_file_was_uploaded(self):
        sync = self.first_import()
        services.receive(self.p("P002"), self.front, 4)
        services.export_airregi(sync)
        with self.assertRaises(services.NeedsUploadConfirmation):
            services.import_airregi(SAMPLE.read_bytes(), "stock.csv")

        # アップロードしていなかった → 入荷+4 は次のファイルに入る
        sync2 = services.import_airregi(SAMPLE.read_bytes(), "stock.csv", previous_uploaded=False)
        self.assertEqual(AirregiSync.objects.get(pk=sync.pk).status, AirregiSync.DISCARDED)
        self.assertEqual(qty(self.p("P002")), 3 + 4)
        self.assertEqual(exported_rows(services.export_airregi(sync2))["P002"], (4, "入荷", "入荷+4"))

        # 今度はアップロードした → Airレジ側は 3+4=7 になっているはず
        sync3 = services.import_airregi(airregi_file({"20002": 7}), "stock.csv", previous_uploaded=True)
        self.assertEqual(sync3.summary["changes"], [])
        self.assertEqual(qty(self.p("P002")), 7)

    def test_existing_stock_brings_airregi_in_line(self):
        p = self.p("P004")
        services.receive(p, self.front, 5)  # このシステムでは5、Airレジでは2
        sync = self.first_import()
        self.assertEqual(qty(p), 5)
        rows = exported_rows(services.export_airregi(sync))
        self.assertEqual(rows["P004"], (3, "入荷", "入荷+5 差の調整"))

    def test_register_unmatched(self):
        sync = self.first_import()
        created = services.register_from_airregi(sync, [("20101", "0"), ("20102", "1")], self.user)
        self.assertEqual([c.code for c in created], ["AR20101", "AR20102-1"])
        rose = Product.objects.get(code="AR20101")
        self.assertEqual(rose.name, "バラ（赤）")
        self.assertEqual(qty(rose), -11)
        self.assertEqual(Product.objects.get(code="AR20102-1").name, "ラッピング S")
        self.assertEqual(AirregiSync.objects.get(pk=sync.pk).summary["unmatched"], [])
        self.assertEqual(exported_rows(services.export_airregi(sync)), {})


class StocktakeTests(Base):
    def test_confirm_writes_differences(self):
        sync = services.import_airregi(SAMPLE.read_bytes(), "stock.csv")
        services.confirm_uploaded(services.export_airregi(sync) and sync)
        p1, p2 = self.p("P001"), self.p("P002")
        st = Stocktake.objects.create(title="2026年末")
        services.save_counts(st, {(p1.pk, self.front.pk): 4, (p2.pk, self.front.pk): None, (p2.pk, self.back.pk): 1})
        services.confirm_stocktake(st, self.user)
        self.assertEqual(qty(p1), 4)
        self.assertEqual((qty(p2, self.front), qty(p2, self.back)), (3, 1))
        with self.assertRaises(services.InventoryError):
            services.save_counts(st, {(p1.pk, self.front.pk): 1})

        sync2 = services.import_airregi(SAMPLE.read_bytes(), "stock.csv")
        rows = exported_rows(services.export_airregi(sync2))
        self.assertEqual(rows["P001"][:2], (-2, "棚卸し・在庫確認"))
        self.assertEqual(rows["P002"][:2], (1, "棚卸し・在庫確認"))


class AlertTests(Base):
    def test_low_stock(self):
        p = self.p("P001")
        p.reorder_point = 3
        p.save()
        services.receive(p, self.front, 3)
        self.assertEqual(services.low_stock_products(), [p])
        services.receive(p, self.front, 1)
        self.assertEqual(services.low_stock_products(), [])
