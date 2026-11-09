from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from inventory import airregi
from inventory.models import AirregiSync, Location, Movement, Product, Stocktake

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "airregi_stock_sample.csv"


class ViewTests(TestCase):
    def setUp(self):
        call_command("setup_shop", verbosity=0)
        self.manager = User.objects.create_user("tencho", password="pw123456", is_staff=True, is_superuser=True)
        self.staff = User.objects.create_user("staff", password="pw123456")
        self.front = Location.objects.get(name="店頭")

    def test_login_required(self):
        r = self.client.get(reverse("home"))
        self.assertRedirects(r, reverse("login") + "?next=/")

    def test_staff_pages(self):
        self.client.force_login(self.staff)
        p = Product.objects.get(code="P001")
        for url in [
            reverse("home"),
            reverse("stock_list"),
            reverse("product_detail", args=[p.pk]),
            reverse("record_select", args=["receive"]),
            reverse("record_form", args=["adjust", p.pk]),
            reverse("history"),
            reverse("stocktake_list"),
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        # 店長専用の画面には入れない
        self.assertRedirects(self.client.get(reverse("airregi_sync")), reverse("home"))
        self.assertRedirects(self.client.get(reverse("product_list")), reverse("home"))

    def test_record_and_undo(self):
        self.client.force_login(self.staff)
        p = Product.objects.get(code="P001")
        r = self.client.post(reverse("record_form", args=["receive", p.pk]), {"location": self.front.pk, "qty": "4"}, follow=True)
        self.assertContains(r, "パキラ 3号を 4鉢 入荷しました。")
        self.assertContains(r, "取り消す")
        self.assertEqual(p.total_qty(), 4)
        m = Movement.objects.get()
        self.client.post(reverse("cancel_movement", args=[m.pk]))
        self.assertEqual(p.total_qty(), 0)

        r = self.client.post(reverse("record_form", args=["waste", p.pk]), {"location": self.front.pk, "qty": "0", "reason": "傷み"}, follow=True)
        self.assertContains(r, "1以上の数を入れてください。")

    def test_move(self):
        self.client.force_login(self.staff)
        p = Product.objects.get(code="P001")
        back = Location.objects.get(name="バックヤード")
        self.client.post(reverse("record_form", args=["receive", p.pk]), {"location": self.front.pk, "qty": "5"})
        self.client.post(reverse("record_form", args=["move", p.pk]), {"location": self.front.pk, "to_location": back.pk, "qty": "2"})
        self.assertEqual(p.stocks.get(location=back).qty, 2)

    def test_airregi_flow_through_screens(self):
        self.client.force_login(self.manager)
        with SAMPLE.open("rb") as f:
            r = self.client.post(reverse("airregi_sync"), {"file": f}, follow=True)
        self.assertContains(r, "取り込みました")
        self.assertContains(r, "まだ登録していない商品（2件）")
        sync = AirregiSync.objects.get()

        self.client.post(reverse("airregi_register", args=[sync.pk]), {"key": ["20101|0"]})
        self.assertTrue(Product.objects.filter(code="AR20101").exists())

        p = Product.objects.get(code="P002")
        self.client.post(reverse("record_form", args=["receive", p.pk]), {"location": self.front.pk, "qty": "2"})
        r = self.client.post(reverse("airregi_export", args=[sync.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("filename*=UTF-8''", r["Content-Disposition"])
        parsed = airregi.parse(r.content, "stock.csv")
        row = next(x for x in parsed.rows if x[airregi.COL_CODE] == "P002")
        self.assertEqual(row[airregi.COL_UPDATE_QTY:airregi.COL_UPDATE_MEMO], ["2", "入荷"])

        self.client.post(reverse("airregi_uploaded", args=[sync.pk]), {"answer": "yes"})
        self.assertEqual(AirregiSync.objects.get().status, AirregiSync.UPLOADED)

    def test_bad_file_shows_error(self):
        self.client.force_login(self.manager)
        from django.core.files.uploadedfile import SimpleUploadedFile

        f = SimpleUploadedFile("x.csv", b"a,b\n1,2\n")
        r = self.client.post(reverse("airregi_sync"), {"file": f}, follow=True)
        self.assertContains(r, "取り込めませんでした")

    def test_stocktake_screens(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("stocktake_new"), {"title": "年末"})
        st = Stocktake.objects.get()
        p = Product.objects.get(code="P001")
        r = self.client.post(
            reverse("stocktake_edit", args=[st.pk]), {"location": self.front.pk, f"count_{p.pk}": "7"}, follow=True
        )
        self.assertContains(r, "店頭の数を保存しました。")
        r = self.client.get(reverse("stocktake_review", args=[st.pk]))
        self.assertContains(r, "+7")
        self.client.post(reverse("stocktake_confirm", args=[st.pk]))
        self.assertEqual(p.total_qty(), 7)
        r = self.client.get(reverse("stocktake_csv", args=[st.pk]))
        self.assertIn("パキラ 3号", r.content.decode("utf-8-sig"))

    def test_product_csv_import(self):
        self.client.force_login(self.manager)
        from django.core.files.uploadedfile import SimpleUploadedFile

        text = "商品コード,商品名,ふりがな,カテゴリ,単位,発注点,日持ち日数,いつもの保管場所,メモ\r\nP101,バラ（赤）,ばら,切り花,本,20,5,フラワーキーパー（冷蔵ケース）,\r\nP001,パキラ 3号（小）,,観葉植物,鉢,4,,店頭,\r\n"
        f = SimpleUploadedFile("p.csv", text.encode("cp932"))
        r = self.client.post(reverse("product_csv_import"), {"file": f}, follow=True)
        self.assertContains(r, "1件 追加、1件 更新")
        self.assertEqual(Product.objects.get(code="P101").shelf_life_days, 5)
        self.assertEqual(Product.objects.get(code="P001").name, "パキラ 3号（小）")

        f = SimpleUploadedFile("p.csv", "商品コード,商品名,いつもの保管場所\r\nP102,x,どこか\r\n".encode())
        r = self.client.post(reverse("product_csv_import"), {"file": f}, follow=True)
        self.assertContains(r, "保管場所「どこか」が登録されていません")
        self.assertFalse(Product.objects.filter(code="P102").exists())
