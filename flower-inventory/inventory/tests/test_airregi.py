from pathlib import Path

from django.test import SimpleTestCase

from inventory import airregi

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "airregi_stock_sample.csv"


class ParseTests(SimpleTestCase):
    def test_parse_sample(self):
        parsed = airregi.parse(SAMPLE.read_bytes(), "stock.csv")
        self.assertEqual(parsed.encoding, "cp932")
        self.assertEqual(parsed.preamble, ["v200"])
        self.assertEqual(parsed.newline, "\r\n")
        self.assertEqual(len(parsed.items), 22)
        first = parsed.items[0]
        self.assertEqual(first.key, ("20001", "0"))
        self.assertEqual(first.name, "パキラ 3号")
        self.assertEqual(first.code, "P001")
        self.assertEqual(first.current_qty, 6)
        self.assertEqual(parsed.items[20].current_qty, -11)
        self.assertEqual(parsed.items[21].display_name, "ラッピング S")

    def test_utf8_with_bom_and_halfwidth_parens(self):
        text = "﻿v200\n" + ",".join(airregi.HEADER).replace("（", "(").replace("）", ")") + "\n1,花,0,,,,,,3,,,,\n"
        parsed = airregi.parse(text.encode("utf-8"), "a.csv")
        self.assertEqual(parsed.encoding, "utf-8-sig")
        self.assertEqual(parsed.items[0].current_qty, 3)

    def test_rejects_other_files(self):
        with self.assertRaises(airregi.AirregiCsvError):
            airregi.parse("a,b,c\n1,2,3\n".encode(), "x.csv")

    def test_rejects_wrong_column_count(self):
        text = "v200\n" + ",".join(airregi.HEADER) + "\n1,花,0,,,,,,3\n"
        with self.assertRaises(airregi.AirregiCsvError):
            airregi.parse(text.encode(), "x.csv")

    def test_rejects_duplicate_keys(self):
        text = "v200\n" + ",".join(airregi.HEADER) + "\n1,花,0,,,,,,3,,,,\n1,花,0,,,,,,3,,,,\n"
        with self.assertRaises(airregi.AirregiCsvError):
            airregi.parse(text.encode(), "x.csv")


class BuildTests(SimpleTestCase):
    def test_roundtrip_without_updates_is_identical(self):
        data = SAMPLE.read_bytes()
        parsed = airregi.parse(data, "stock.csv")
        self.assertEqual(airregi.build(parsed, {}), data)

    def test_only_three_columns_change(self):
        data = SAMPLE.read_bytes()
        parsed = airregi.parse(data, "stock.csv")
        out = airregi.build(parsed, {("20002", "0"): airregi.RowUpdate(5, "入荷", "入荷+5")})
        before = data.decode("cp932").splitlines()
        after = out.decode("cp932").splitlines()
        self.assertEqual(len(before), len(after))
        diff = [(b, a) for b, a in zip(before, after) if b != a]
        self.assertEqual(diff, [("20002,パキラ 6号,0,,,観葉植物,P002,,3,,,,", "20002,パキラ 6号,0,,,観葉植物,P002,,3,5,入荷,入荷+5,")])
        self.assertEqual(after[0], "v200")

    def test_existing_update_cells_are_cleared(self):
        text = "v200\n" + ",".join(airregi.HEADER) + "\n1,花,0,,,,,,3,9,入荷,古いメモ,\n"
        out = airregi.build(airregi.parse(text.encode(), "a.csv"), {})
        self.assertIn("1,花,0,,,,,,3,,,,", out.decode())

    def test_quote_all_is_preserved(self):
        header = ",".join(f'"{h}"' for h in airregi.HEADER)
        row = ",".join(f'"{v}"' for v in ["1", "花", "0", "", "", "", "", "", "3", "", "", "", ""])
        text = f"v200\r\n{header}\r\n{row}\r\n"
        out = airregi.build(airregi.parse(text.encode(), "a.csv"), {})
        self.assertEqual(out.decode(), text)

    def test_rejects_out_of_range_and_bad_kind(self):
        parsed = airregi.parse(SAMPLE.read_bytes(), "stock.csv")
        with self.assertRaises(airregi.AirregiCsvError):
            airregi.build(parsed, {("20001", "0"): airregi.RowUpdate(10_000_000, "入荷", "")})
        with self.assertRaises(airregi.AirregiCsvError):
            airregi.build(parsed, {("20001", "0"): airregi.RowUpdate(1, "制作", "")})
        with self.assertRaises(airregi.AirregiCsvError):
            airregi.build(parsed, {("99999", "0"): airregi.RowUpdate(1, "入荷", "")})


class MemoTests(SimpleTestCase):
    def test_sanitize(self):
        self.assertEqual(airregi.sanitize_memo("ﾊﾟｷﾗ①入荷"), "パキラ1入荷")
        self.assertEqual(airregi.sanitize_memo("棚卸し―確認∥"), "棚卸し確認")
        self.assertEqual(len(airregi.sanitize_memo("あ" * 50)), 30)
        self.assertEqual(airregi.sanitize_memo("🌸入荷"), "入荷")
