"""在庫を動かす処理。画面（views）からはここの関数だけを呼ぶ。"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from . import airregi
from .models import (
    AirregiSync,
    AirregiSyncLine,
    Location,
    Movement,
    Product,
    Stock,
    Stocktake,
    StocktakeLine,
)


class InventoryError(ValueError):
    pass


# ---------------------------------------------------------------- 入出庫


def _apply(product, location, kind, qty, user=None, reason="", note="", group="", cancel_of=None):
    if qty == 0:
        return None
    stock, _ = Stock.objects.get_or_create(product=product, location=location)
    Stock.objects.filter(pk=stock.pk).update(qty=F("qty") + qty)
    return Movement.objects.create(
        product=product,
        location=location,
        kind=kind,
        qty=qty,
        reason=reason,
        note=note,
        user=user if (user and user.is_authenticated) else None,
        group=group or uuid.uuid4().hex,
        cancel_of=cancel_of,
        needs_send=kind in Movement.AIRREGI_KIND,
    )


def _positive(qty):
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise InventoryError("数を入れてください。")
    if qty <= 0:
        raise InventoryError("1以上の数を入れてください。")
    return qty


@transaction.atomic
def receive(product, location, qty, user=None, note=""):
    return [_apply(product, location, Movement.RECEIVE, _positive(qty), user, note=note)]


@transaction.atomic
def waste(product, location, qty, reason, user=None, note=""):
    return [_apply(product, location, Movement.WASTE, -_positive(qty), user, reason=reason, note=note)]


@transaction.atomic
def offsale(product, location, qty, user=None, note=""):
    return [_apply(product, location, Movement.OFFSALE, -_positive(qty), user, note=note)]


@transaction.atomic
def adjust(product, location, new_qty, user=None, note=""):
    try:
        new_qty = int(new_qty)
    except (TypeError, ValueError):
        raise InventoryError("正しい数を入れてください。")
    if new_qty < 0:
        raise InventoryError("0以上の数を入れてください。")
    current = Stock.objects.filter(product=product, location=location).values_list("qty", flat=True).first() or 0
    if new_qty == current:
        raise InventoryError("今の数と同じです。")
    return [_apply(product, location, Movement.ADJUST, new_qty - current, user, note=note)]


@transaction.atomic
def move(product, from_location, to_location, qty, user=None, note=""):
    qty = _positive(qty)
    if from_location == to_location:
        raise InventoryError("移動元と移動先が同じです。")
    group = uuid.uuid4().hex
    return [
        _apply(product, from_location, Movement.MOVE, -qty, user, note=note, group=group),
        _apply(product, to_location, Movement.MOVE, qty, user, note=note, group=group),
    ]


CANCELLABLE = {Movement.RECEIVE, Movement.WASTE, Movement.MOVE, Movement.ADJUST, Movement.OFFSALE}


def can_cancel(movement):
    return (
        movement.kind in CANCELLABLE
        and movement.cancel_of_id is None
        and not movement.cancelled_by.exists()
    )


@transaction.atomic
def cancel(movement, user=None):
    if not can_cancel(movement):
        raise InventoryError("この記録は取り消せません。")
    group = uuid.uuid4().hex
    created = []
    for m in Movement.objects.filter(group=movement.group).order_by("id"):
        created.append(
            _apply(m.product, m.location, m.kind, -m.qty, user, reason=m.reason, note="取り消し", group=group, cancel_of=m)
        )
    return created


def _deduct_sale(product, qty, group):
    """Airレジで売れた分を、基本の場所 → ほかの場所の順に減らす。"""
    default = product.default_location or Location.default()
    stocks = list(Stock.objects.filter(product=product, qty__gt=0).select_related("location"))
    stocks.sort(key=lambda s: (s.location_id != default.pk, s.location.sort_order, s.location_id))
    remaining = qty
    for s in stocks:
        take = min(s.qty, remaining)
        _apply(product, s.location, Movement.AIRREGI_SALE, -take, group=group)
        remaining -= take
        if remaining == 0:
            return
    _apply(product, default, Movement.AIRREGI_SALE, -remaining, group=group)


# ---------------------------------------------------------------- Airレジ同期


class NeedsUploadConfirmation(InventoryError):
    def __init__(self, sync):
        super().__init__("前回作ったファイルをAirレジにアップロードしたか確認してください。")
        self.sync = sync


def pending_upload_sync():
    return AirregiSync.objects.filter(status=AirregiSync.EXPORTED).first()


def latest_sync():
    return AirregiSync.objects.exclude(status=AirregiSync.DISCARDED).first()


@transaction.atomic
def confirm_uploaded(sync):
    if sync.status != AirregiSync.EXPORTED:
        raise InventoryError("アップロード待ちのファイルではありません。")
    for line in sync.lines.exclude(export_qty=None).select_related("product"):
        p = line.product
        p.airregi_baseline = (p.airregi_baseline or 0) + line.export_qty
        p.save(update_fields=["airregi_baseline"])
    sync.exported_movements.update(sent=True)
    sync.status = AirregiSync.UPLOADED
    sync.uploaded_at = timezone.now()
    sync.save()


@transaction.atomic
def discard_export(sync):
    """作ったファイルをAirレジにアップロードしなかった場合。"""
    Movement.objects.filter(export_sync=sync).update(export_sync=None)
    sync.lines.update(export_qty=None, export_kind="", export_memo="")
    sync.status = AirregiSync.DISCARDED
    sync.save()


def _unique_code(base):
    code, n = base, 2
    while Product.objects.filter(code=code).exists():
        code = f"{base}-{n}"
        n += 1
    return code


@transaction.atomic
def import_airregi(data: bytes, filename: str, user=None, previous_uploaded: bool | None = None):
    """Airレジからダウンロードした編集用CSVを取り込む。

    前回の同期で確定した在庫数（airregi_baseline）と今回の「現在在庫数」の差を、
    Airレジ側で起きた変動（主に会計による販売）として在庫に反映する。
    """
    parsed = airregi.parse(data, filename)

    pending = pending_upload_sync()
    if pending:
        if previous_uploaded is None:
            raise NeedsUploadConfirmation(pending)
        if previous_uploaded:
            confirm_uploaded(pending)
        else:
            discard_export(pending)
    AirregiSync.objects.filter(status=AirregiSync.IMPORTED).update(status=AirregiSync.DISCARDED)

    sync = AirregiSync.objects.create(
        filename=filename, raw=data, user=user if (user and user.is_authenticated) else None
    )
    linked = {p.airregi_key: p for p in Product.objects.exclude(airregi_product_id="")}
    unlinked_by_code = {p.code: p for p in Product.objects.filter(airregi_product_id="")}

    changes, new_links, unmatched = [], [], []
    seen = set()
    for item in parsed.items:
        product = linked.get(item.key)
        if product is None and item.code and item.code in unlinked_by_code:
            product = unlinked_by_code.pop(item.code)
            product.airregi_product_id, product.airregi_variation_id = item.key
            product.airregi_baseline = None
            product.save()
            new_links.append(product.name)
        if product is None:
            unmatched.append(
                {
                    "product_id": item.product_id,
                    "variation_id": item.variation_id,
                    "name": item.display_name,
                    "category": item.category,
                    "code": item.code,
                    "barcode": item.barcode,
                    "current_qty": item.current_qty,
                }
            )
            continue
        seen.add(product.pk)
        if item.current_qty is None:
            continue
        change = 0
        if product.airregi_baseline is None and not product.movements.exists():
            # 初めてひも付けた商品で、このシステムにまだ記録がなければ、Airレジの在庫数から始める
            if item.current_qty:
                _apply(
                    product,
                    product.default_location or Location.default(),
                    Movement.INITIAL,
                    item.current_qty,
                    user,
                    note="Airレジの在庫数から開始",
                )
        elif product.airregi_baseline is not None:
            change = item.current_qty - product.airregi_baseline
            group = uuid.uuid4().hex
            if change < 0:
                _deduct_sale(product, -change, group)
            elif change > 0:
                _apply(product, product.default_location or Location.default(), Movement.AIRREGI_ADJUST, change, group=group)
        product.airregi_baseline = item.current_qty
        product.save(update_fields=["airregi_baseline"])
        AirregiSyncLine.objects.create(
            sync=sync, product=product, airregi_qty=item.current_qty, change_from_airregi=change
        )
        if change:
            changes.append({"name": product.name, "change": change, "unit": product.unit})

    missing = [p.name for p in linked.values() if p.pk not in seen and p.is_active]
    sync.summary = {
        "rows": len(parsed.items),
        "encoding": parsed.encoding,
        "changes": changes,
        "new_links": new_links,
        "unmatched": unmatched,
        "missing": missing,
    }
    sync.save()
    return sync


@transaction.atomic
def register_from_airregi(sync, keys, user=None):
    """取り込んだファイルの中から、まだ登録していない商品を登録する。"""
    if sync != latest_sync() or sync.status != AirregiSync.IMPORTED:
        raise InventoryError("最新の取り込み結果から登録してください。")
    wanted = set(keys)
    remaining, created = [], []
    location = Location.default()
    for u in sync.summary.get("unmatched", []):
        key = (u["product_id"], u["variation_id"])
        if key not in wanted:
            remaining.append(u)
            continue
        base = u["code"] or (
            f"AR{u['product_id']}" + (f"-{u['variation_id']}" if u["variation_id"] not in ("", "0") else "")
        )
        product = Product.objects.create(
            code=_unique_code(base),
            name=u["name"],
            category=u["category"],
            default_location=location,
            airregi_product_id=u["product_id"],
            airregi_variation_id=u["variation_id"],
            airregi_baseline=u["current_qty"],
        )
        if u["current_qty"]:
            _apply(product, location, Movement.INITIAL, u["current_qty"], user, note="Airレジから登録")
        AirregiSyncLine.objects.create(sync=sync, product=product, airregi_qty=u["current_qty"])
        created.append(product)
    sync.summary["unmatched"] = remaining
    sync.save()
    return created


def _export_for_line(line, pending_movements):
    product = line.product
    delta = product.total_qty() - (line.airregi_qty or 0)
    if delta == 0:
        return None
    by_kind = defaultdict(int)
    for m in pending_movements:
        by_kind[m.kind] += m.qty
    by_kind = {k: v for k, v in by_kind.items() if v}
    if by_kind:
        main = max(by_kind, key=lambda k: abs(by_kind[k]))
        kind = Movement.AIRREGI_KIND[main]
        labels = dict(Movement.KIND_CHOICES)
        memo = " ".join(f"{labels[k].replace('Airレジを通さない販売', '店外販売')}{v:+d}" for k, v in by_kind.items())
        if sum(by_kind.values()) != delta:
            memo += " 差の調整"
    else:
        kind, memo = "その他", "差の調整"
    return airregi.RowUpdate(qty=delta, kind=kind, memo=memo)


@transaction.atomic
def export_airregi(sync) -> bytes:
    """Airレジにアップロードするファイルを作る（足し引きする数を書き込む）。"""
    if sync.status == AirregiSync.EXPORTED:
        return build_export_file(sync)
    if sync.status != AirregiSync.IMPORTED or sync != latest_sync():
        raise InventoryError("最新の取り込み結果からファイルを作ってください。")

    for line in sync.lines.select_related("product"):
        pending = list(
            Movement.objects.filter(product=line.product, needs_send=True, sent=False, export_sync=None)
        )
        upd = _export_for_line(line, pending)
        Movement.objects.filter(pk__in=[m.pk for m in pending]).update(export_sync=sync)
        if upd:
            line.export_qty, line.export_kind = upd.qty, upd.kind
            line.export_memo = airregi.sanitize_memo(upd.memo)
            line.save()
    sync.status = AirregiSync.EXPORTED
    sync.exported_at = timezone.now()
    sync.save()
    return build_export_file(sync)


def build_export_file(sync) -> bytes:
    parsed = airregi.parse(bytes(sync.raw), sync.filename)
    updates = {
        line.product.airregi_key: airregi.RowUpdate(line.export_qty, line.export_kind, line.export_memo)
        for line in sync.lines.exclude(export_qty=None).select_related("product")
    }
    return airregi.build(parsed, updates)


def unsent_count():
    return Movement.objects.filter(needs_send=True, sent=False, export_sync=None).count()


# ---------------------------------------------------------------- 棚卸し


@transaction.atomic
def save_counts(stocktake, counts: dict):
    """counts: {(product_id, location_id): int または None}"""
    if stocktake.status != Stocktake.DRAFT:
        raise InventoryError("確定済みの棚卸しは変更できません。")
    for (pid, lid), counted in counts.items():
        if counted is not None and counted < 0:
            raise InventoryError("実際の数は0以上で入れてください。")
        line, _ = StocktakeLine.objects.get_or_create(stocktake=stocktake, product_id=pid, location_id=lid)
        line.counted = counted
        line.save()


@transaction.atomic
def confirm_stocktake(stocktake, user=None):
    if stocktake.status != Stocktake.DRAFT:
        raise InventoryError("すでに確定しています。")
    for line in stocktake.lines.exclude(counted=None).select_related("product", "location"):
        current = (
            Stock.objects.filter(product=line.product, location=line.location).values_list("qty", flat=True).first()
            or 0
        )
        line.system_qty = current
        line.save()
        _apply(
            line.product, line.location, Movement.STOCKTAKE, line.counted - current, user, note=stocktake.title
        )
    stocktake.status = Stocktake.DONE
    stocktake.completed_at = timezone.now()
    stocktake.save()


# ---------------------------------------------------------------- 通知


def low_stock_products():
    qs = Product.objects.filter(is_active=True, reorder_point__gt=0).annotate(total=Sum("stocks__qty"))
    return [p for p in qs if (p.total or 0) <= p.reorder_point]


def shelf_life_alerts(today=None):
    today = today or timezone.localdate()
    qs = (
        Product.objects.filter(is_active=True, shelf_life_days__isnull=False)
        .annotate(total=Sum("stocks__qty"))
    )
    alerts = []
    for p in qs:
        last = p.movements.filter(kind=Movement.RECEIVE, qty__gt=0).order_by("-created_at").first()
        if not last or (p.total or 0) <= 0:
            continue
        limit = timezone.localtime(last.created_at).date() + timedelta(days=p.shelf_life_days)
        if limit - timedelta(days=1) <= today:
            alerts.append({"product": p, "limit": limit, "received": last.created_at})
    return alerts
