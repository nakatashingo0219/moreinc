"""Airレジ「在庫一括編集CSV」の読み込みと書き出し。

Django に依存しない純粋な処理だけをここに置く。

ファイル形式（Airレジ FAQ と画面例で確認した内容）:
  1行目    : 形式のバージョン（例 "v200"）
  2行目    : 項目名（13列）
  3行目以降: 商品・バリエーションごとに1行

編集してよいのは「更新在庫数」「更新種別」「更新メモ」の3列だけ。
ファイル名・項目名・項目数・商品ID・バリエーションIDは変えてはいけない。
文字コードは実ファイルで未確認のため、読み込んだときの文字コード・改行・
引用符の付け方をそのまま保って書き出す。
"""

from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass, field

HEADER = [
    "商品ID",
    "商品名",
    "バリエーションID",
    "バリエーション（種別1）",
    "バリエーション（種別2）",
    "カテゴリー",
    "商品コード",
    "バーコード",
    "現在在庫数",
    "更新在庫数",
    "更新種別",
    "更新メモ",
    "商品備考",
]

COL_PRODUCT_ID = 0
COL_NAME = 1
COL_VARIATION_ID = 2
COL_VAR1 = 3
COL_VAR2 = 4
COL_CATEGORY = 5
COL_CODE = 6
COL_BARCODE = 7
COL_CURRENT = 8
COL_UPDATE_QTY = 9
COL_UPDATE_KIND = 10
COL_UPDATE_MEMO = 11

# Airレジが受け付ける「更新種別」
UPDATE_KINDS = (
    "入荷",
    "販売",
    "返品",
    "破損・不良品",
    "紛失・盗難",
    "EC販売",
    "棚卸し・在庫確認",
    "その他",
)

QTY_MIN = -9_999_999
QTY_MAX = 9_999_999
MEMO_MAX_LEN = 30

# NFKC で変換したあとも残る、Airレジで使えない・推奨されない文字
_MEMO_DROP_CHARS = set("∵∩∪⋂⋒¢£¬―∥¥\\￠￡￢～－")


class AirregiCsvError(ValueError):
    """ファイルがAirレジの在庫一括編集CSVとして読めないとき。"""


@dataclass
class AirregiRow:
    line_index: int  # rows の中の位置
    product_id: str
    variation_id: str
    name: str
    variation1: str
    variation2: str
    category: str
    code: str
    barcode: str
    current_qty: int | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.product_id, self.variation_id)

    @property
    def display_name(self) -> str:
        parts = [self.name] + [v for v in (self.variation1, self.variation2) if v]
        return " ".join(parts)


@dataclass
class ParsedFile:
    filename: str
    encoding: str
    newline: str
    quote_all: bool
    preamble: list[str]  # 項目名より前の行（"v200" など）。そのまま書き戻す
    header: list[str]
    rows: list[list[str]]
    items: list[AirregiRow] = field(default_factory=list)


@dataclass
class RowUpdate:
    qty: int
    kind: str
    memo: str


def _decode(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp932"), "cp932"
    except UnicodeDecodeError as e:
        raise AirregiCsvError("ファイルの文字コードを読み取れませんでした。") from e


def _norm_header(cell: str) -> str:
    return unicodedata.normalize("NFKC", cell).strip().replace(" ", "")


def _parse_int(value: str) -> int | None:
    v = value.strip().replace(",", "")
    if v == "":
        return None
    try:
        return int(v)
    except ValueError:
        try:
            f = float(v)
        except ValueError:
            return None
        return int(f) if f.is_integer() else None


def parse(data: bytes, filename: str) -> ParsedFile:
    text, encoding = _decode(data)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    expected = [_norm_header(h) for h in HEADER]
    header_at = None
    for i, line in enumerate(lines[:5]):
        cells = next(csv.reader([line]), [])
        if cells and _norm_header(cells[0]) == expected[0]:
            header_at = i
            break
    if header_at is None:
        raise AirregiCsvError(
            "「商品ID」の項目名が見つかりません。Airレジの「在庫数をCSVで編集する」から"
            "ダウンロードしたファイルを選んでください。"
        )

    preamble = lines[:header_at]
    body = newline.join(lines[header_at:])
    all_rows = list(csv.reader(io.StringIO(body)))
    header, rows = all_rows[0], all_rows[1:]
    if [_norm_header(h) for h in header] != expected:
        raise AirregiCsvError(
            "項目名が想定と違います（想定：" + "、".join(HEADER) + "）。"
            "Airレジの仕様が変わった可能性があります。"
        )
    quote_all = lines[header_at].startswith('"')

    parsed = ParsedFile(
        filename=filename,
        encoding=encoding,
        newline=newline,
        quote_all=quote_all,
        preamble=preamble,
        header=header,
        rows=rows,
    )
    for idx, row in enumerate(rows):
        if not any(c.strip() for c in row):
            continue
        if len(row) != len(HEADER):
            raise AirregiCsvError(f"{idx + 1}件目の行の項目数が{len(row)}個です（{len(HEADER)}個のはず）。")
        pid = row[COL_PRODUCT_ID].strip()
        if not pid:
            raise AirregiCsvError(f"{idx + 1}件目の行に商品IDがありません。")
        parsed.items.append(
            AirregiRow(
                line_index=idx,
                product_id=pid,
                variation_id=row[COL_VARIATION_ID].strip(),
                name=row[COL_NAME].strip(),
                variation1=row[COL_VAR1].strip(),
                variation2=row[COL_VAR2].strip(),
                category=row[COL_CATEGORY].strip(),
                code=row[COL_CODE].strip(),
                barcode=row[COL_BARCODE].strip(),
                current_qty=_parse_int(row[COL_CURRENT]),
            )
        )
    keys = [it.key for it in parsed.items]
    if len(keys) != len(set(keys)):
        raise AirregiCsvError("同じ商品ID・バリエーションIDの行が重複しています。")
    return parsed


def sanitize_memo(memo: str, encoding: str = "cp932") -> str:
    """Airレジの「更新メモ」で使える文字だけにして30文字以内に切る。"""
    text = unicodedata.normalize("NFKC", memo or "")
    out = []
    for ch in text:
        if ch in _MEMO_DROP_CHARS or ch in "\r\n\t":
            continue
        try:
            ch.encode("cp932")
        except UnicodeEncodeError:
            continue
        out.append(ch)
    return "".join(out).strip()[:MEMO_MAX_LEN]


def validate_update(update: RowUpdate, current_qty: int | None) -> None:
    if not (QTY_MIN <= update.qty <= QTY_MAX):
        raise AirregiCsvError(f"更新在庫数 {update.qty} が範囲外です。")
    if current_qty is not None and not (QTY_MIN <= current_qty + update.qty <= QTY_MAX):
        raise AirregiCsvError("更新後の在庫数が範囲外になります。")
    if update.kind and update.kind not in UPDATE_KINDS:
        raise AirregiCsvError(f"更新種別「{update.kind}」はAirレジで使えません。")


def build(parsed: ParsedFile, updates: dict[tuple[str, str], RowUpdate]) -> bytes:
    """元のファイルと同じ形式で、3列だけを書き換えたCSVを作る。

    updates にない行は3列とも空欄にする（＝変更しない）。
    """
    rows = [list(r) for r in parsed.rows]
    by_key = {it.key: it for it in parsed.items}
    for row in rows:
        if len(row) == len(HEADER):
            row[COL_UPDATE_QTY] = row[COL_UPDATE_KIND] = row[COL_UPDATE_MEMO] = ""
    for key, upd in updates.items():
        item = by_key.get(key)
        if item is None:
            raise AirregiCsvError(f"商品ID {key[0]} / バリエーションID {key[1]} がファイルにありません。")
        validate_update(upd, item.current_qty)
        row = rows[item.line_index]
        row[COL_UPDATE_QTY] = str(upd.qty)
        row[COL_UPDATE_KIND] = upd.kind
        row[COL_UPDATE_MEMO] = sanitize_memo(upd.memo)

    buf = io.StringIO(newline="")
    for line in parsed.preamble:
        buf.write(line + parsed.newline)
    writer = csv.writer(
        buf,
        lineterminator=parsed.newline,
        quoting=csv.QUOTE_ALL if parsed.quote_all else csv.QUOTE_MINIMAL,
    )
    writer.writerow(parsed.header)
    writer.writerows(rows)
    return buf.getvalue().encode(parsed.encoding)
