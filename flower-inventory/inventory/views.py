import csv
import io
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import airregi, services
from .models import AirregiSync, Location, Movement, Product, Stock, Stocktake, StocktakeLine

# 画面から記録できる種類
RECORD_KINDS = {
    "receive": {"label": "入荷", "verb": "入荷します", "icon": "＋", "tone": "in"},
    "waste": {"label": "廃棄", "verb": "廃棄します", "icon": "−", "tone": "out"},
    "move": {"label": "移動", "verb": "移動します", "icon": "⇄", "tone": "move"},
    "adjust": {"label": "数の修正", "verb": "に直します", "icon": "✎", "tone": "fix"},
    "offsale": {"label": "店外販売", "verb": "販売（Airレジ以外）として減らします", "icon": "¥", "tone": "out"},
}


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, "この画面は店長（管理者）だけが使えます。")
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapper


def _products_with_stock(qs=None):
    qs = qs if qs is not None else Product.objects.filter(is_active=True)
    return qs.annotate(total=Sum("stocks__qty")).prefetch_related(
        Prefetch("stocks", queryset=Stock.objects.select_related("location"))
    )


def _categories():
    return (
        Product.objects.filter(is_active=True)
        .exclude(category="")
        .values_list("category", flat=True)
        .distinct()
        .order_by("category")
    )


@login_required
def home(request):
    negative = _products_with_stock().filter(total__lt=0)
    ctx = {
        "kinds": RECORD_KINDS,
        "low": services.low_stock_products(),
        "shelf": services.shelf_life_alerts(),
        "negative": negative,
        "unsent": services.unsent_count(),
        "pending": services.pending_upload_sync(),
        "recent": Movement.objects.select_related("product", "location", "user")[:8],
    }
    return render(request, "inventory/home.html", ctx)


@login_required
def stock_list(request):
    locations = list(Location.objects.all())
    products = list(_products_with_stock())
    for p in products:
        by_loc = {s.location_id: s.qty for s in p.stocks.all()}
        p.by_location = [by_loc.get(loc.pk) for loc in locations]
        p.total = p.total or 0
        p.low = p.reorder_point > 0 and p.total <= p.reorder_point
    return render(
        request,
        "inventory/stock_list.html",
        {"products": products, "locations": locations, "categories": _categories()},
    )


@login_required
def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    stocks = Stock.objects.filter(product=product).select_related("location")
    movements = list(product.movements.select_related("location", "user")[:50])
    for m in movements:
        m.cancellable = services.can_cancel(m)
    return render(
        request,
        "inventory/product_detail.html",
        {"product": product, "stocks": stocks, "movements": movements, "kinds": RECORD_KINDS},
    )


@login_required
def record_select(request, kind):
    if kind not in RECORD_KINDS:
        return redirect("home")
    products = list(_products_with_stock())
    for p in products:
        p.total = p.total or 0
    return render(
        request,
        "inventory/record_select.html",
        {"kind": kind, "k": RECORD_KINDS[kind], "products": products, "categories": _categories()},
    )


@login_required
def record_form(request, kind, pk):
    if kind not in RECORD_KINDS:
        return redirect("home")
    product = get_object_or_404(Product, pk=pk, is_active=True)
    locations = list(Location.objects.all())
    stock_map = dict(Stock.objects.filter(product=product).values_list("location_id", "qty"))
    for loc in locations:
        loc.qty = stock_map.get(loc.pk, 0)
    default_loc = product.default_location or Location.default()
    # 廃棄・移動・修正は、在庫がある場所を初期値にする
    if kind in ("waste", "move", "adjust", "offsale") and stock_map.get(default_loc.pk, 0) <= 0:
        with_stock = [loc for loc in locations if loc.qty > 0]
        if with_stock:
            default_loc = with_stock[0]

    if request.method == "POST":
        try:
            loc = get_object_or_404(Location, pk=request.POST.get("location"))
            q = request.POST.get("qty")
            note = request.POST.get("note", "")[:200]
            if kind == "receive":
                services.receive(product, loc, q, request.user, note)
                msg = f"{product.name}を {q}{product.unit} 入荷しました。"
            elif kind == "waste":
                reason = request.POST.get("reason") or "その他"
                services.waste(product, loc, q, reason, request.user, note)
                msg = f"{product.name}を {q}{product.unit} 廃棄しました（{reason}）。"
            elif kind == "offsale":
                services.offsale(product, loc, q, request.user, note)
                msg = f"{product.name}を {q}{product.unit} 店外販売として減らしました。"
            elif kind == "move":
                to = get_object_or_404(Location, pk=request.POST.get("to_location"))
                services.move(product, loc, to, q, request.user, note)
                msg = f"{product.name}を {q}{product.unit}、{loc.name}から{to.name}へ移動しました。"
            else:
                services.adjust(product, loc, q, request.user, note)
                msg = f"{product.name}（{loc.name}）を {q}{product.unit} に直しました。"
        except services.InventoryError as e:
            messages.error(request, str(e))
            return redirect(request.path)
        last = product.movements.order_by("-id").first()
        messages.success(request, msg, extra_tags=f"undo:{last.pk}" if last else "")
        return redirect(request.POST.get("next") or "home")

    other_loc = next((loc for loc in locations if loc.pk != default_loc.pk), None)
    return render(
        request,
        "inventory/record_form.html",
        {
            "kind": kind,
            "k": RECORD_KINDS[kind],
            "product": product,
            "locations": locations,
            "default_loc": default_loc,
            "other_loc": other_loc,
            "reasons": Movement.WASTE_REASONS,
            "next": request.GET.get("next", ""),
        },
    )


@login_required
@require_POST
def cancel_movement(request, pk):
    m = get_object_or_404(Movement, pk=pk)
    try:
        services.cancel(m, request.user)
        messages.success(request, f"{m.product.name}の「{m.get_kind_display()}」を取り消しました。")
    except services.InventoryError as e:
        messages.error(request, str(e))
    return redirect(request.POST.get("next") or "home")


@login_required
def history(request):
    qs = Movement.objects.select_related("product", "location", "user")
    kind = request.GET.get("kind", "")
    if kind:
        qs = qs.filter(kind=kind)
    movements = list(qs[:200])
    for m in movements:
        m.cancellable = services.can_cancel(m)
    return render(
        request,
        "inventory/history.html",
        {"movements": movements, "kind": kind, "kind_choices": Movement.KIND_CHOICES},
    )


# ---------------------------------------------------------------- 棚卸し


@login_required
def stocktake_list(request):
    return render(request, "inventory/stocktake_list.html", {"stocktakes": Stocktake.objects.all()[:30]})


@admin_required
@require_POST
def stocktake_new(request):
    if Stocktake.objects.filter(status=Stocktake.DRAFT).exists():
        st = Stocktake.objects.filter(status=Stocktake.DRAFT).first()
        messages.info(request, "入力中の棚卸しがあります。続きから入力してください。")
        return redirect("stocktake_edit", pk=st.pk)
    title = request.POST.get("title") or f"{timezone.localdate():%Y年%m月%d日}の棚卸し"
    st = Stocktake.objects.create(title=title[:100], user=request.user)
    return redirect("stocktake_edit", pk=st.pk)


@login_required
def stocktake_edit(request, pk):
    st = get_object_or_404(Stocktake, pk=pk)
    locations = list(Location.objects.all())
    loc_id = int(request.GET.get("location") or request.POST.get("location") or locations[0].pk)
    location = next((loc for loc in locations if loc.pk == loc_id), locations[0])

    if request.method == "POST" and st.status == Stocktake.DRAFT:
        counts = {}
        for key, value in request.POST.items():
            if key.startswith("count_"):
                pid = int(key[6:])
                value = value.strip()
                counts[(pid, location.pk)] = int(value) if value.lstrip("-").isdigit() else None
        try:
            services.save_counts(st, counts)
            messages.success(request, f"{location.name}の数を保存しました。")
        except services.InventoryError as e:
            messages.error(request, str(e))
        nxt = request.POST.get("go_location") or location.pk
        return redirect(f"{reverse('stocktake_edit', args=[st.pk])}?location={nxt}")

    stock_map = dict(Stock.objects.filter(location=location).values_list("product_id", "qty"))
    lines = {
        line.product_id: line
        for line in StocktakeLine.objects.filter(stocktake=st, location=location)
    }
    rows = []
    for p in Product.objects.filter(is_active=True):
        here = p.pk in stock_map or p.default_location_id == location.pk or p.pk in lines
        line = lines.get(p.pk)
        rows.append(
            {
                "product": p,
                "system": stock_map.get(p.pk, 0),
                "counted": line.counted if line else None,
                "line": line,
                "here": here,
            }
        )
    rows.sort(key=lambda r: (not r["here"], r["product"].code))
    progress = []
    for loc in locations:
        n = StocktakeLine.objects.filter(stocktake=st, location=loc).exclude(counted=None).count()
        progress.append({"location": loc, "done": n})
    return render(
        request,
        "inventory/stocktake_edit.html",
        {
            "st": st,
            "location": location,
            "locations": locations,
            "next_location": next((l for l in locations[locations.index(location) + 1:]), None),
            "rows": rows,
            "progress": progress,
        },
    )


@login_required
def stocktake_review(request, pk):
    st = get_object_or_404(Stocktake, pk=pk)
    lines = list(st.lines.exclude(counted=None).select_related("product", "location"))
    for line in lines:
        if st.status == Stocktake.DONE:
            line.system = line.system_qty
        else:
            line.system = (
                Stock.objects.filter(product=line.product, location=line.location)
                .values_list("qty", flat=True)
                .first()
                or 0
            )
        line.diff = line.counted - (line.system or 0)
    lines.sort(key=lambda l: (l.diff == 0, l.location.sort_order, l.product.code))
    return render(request, "inventory/stocktake_review.html", {"st": st, "lines": lines})


@admin_required
@require_POST
def stocktake_confirm(request, pk):
    st = get_object_or_404(Stocktake, pk=pk)
    try:
        services.confirm_stocktake(st, request.user)
        messages.success(request, f"「{st.title}」を確定しました。次のAirレジ同期で差がAirレジにも反映されます。")
    except services.InventoryError as e:
        messages.error(request, str(e))
    return redirect("stocktake_review", pk=st.pk)


@login_required
def stocktake_csv(request, pk):
    st = get_object_or_404(Stocktake, pk=pk)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["棚卸し", "保管場所", "商品コード", "商品名", "単位", "システム上の数", "実際の数", "差"])
    for line in st.lines.exclude(counted=None).select_related("product", "location"):
        system = line.system_qty if line.system_qty is not None else ""
        diff = line.counted - line.system_qty if line.system_qty is not None else ""
        w.writerow([st.title, line.location.name, line.product.code, line.product.name, line.product.unit, system, line.counted, diff])
    resp = HttpResponse(buf.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="stocktake_{st.pk}.csv"'
    return resp


# ---------------------------------------------------------------- Airレジ同期


@admin_required
def airregi_sync(request):
    pending = services.pending_upload_sync()
    latest = services.latest_sync()
    if request.method == "POST" and request.FILES.get("file"):
        f = request.FILES["file"]
        answer = request.POST.get("previous_uploaded")
        prev = {"yes": True, "no": False}.get(answer)
        try:
            services.import_airregi(f.read(), f.name, request.user, previous_uploaded=prev)
            messages.success(request, "Airレジのファイルを取り込みました。内容を確認してください。")
        except services.NeedsUploadConfirmation:
            messages.error(request, "前回作ったファイルをAirレジにアップロードしたかどうかを選んでください。")
        except (airregi.AirregiCsvError, services.InventoryError) as e:
            messages.error(request, f"取り込めませんでした：{e}")
        return redirect("airregi_sync")

    lines = []
    if latest:
        lines = list(latest.lines.select_related("product").order_by("product__code"))
        for line in lines:
            line.current_total = line.product.total_qty()
            line.planned = (
                line.export_qty
                if latest.status != AirregiSync.IMPORTED
                else line.current_total - (line.airregi_qty or 0)
            )
    return render(
        request,
        "inventory/airregi_sync.html",
        {
            "pending": pending,
            "latest": latest,
            "lines": lines,
            "changed_lines": [l for l in lines if l.planned],
            "unsent": services.unsent_count(),
            "history": AirregiSync.objects.all()[:10],
        },
    )


@admin_required
@require_POST
def airregi_register(request, pk):
    sync = get_object_or_404(AirregiSync, pk=pk)
    keys = [tuple(v.split("|", 1)) for v in request.POST.getlist("key")]
    if not keys:
        messages.error(request, "登録する商品にチェックを入れてください。")
        return redirect("airregi_sync")
    try:
        created = services.register_from_airregi(sync, keys, request.user)
        messages.success(request, f"{len(created)}件の商品を登録しました。")
    except services.InventoryError as e:
        messages.error(request, str(e))
    return redirect("airregi_sync")


@admin_required
@require_POST
def airregi_export(request, pk):
    sync = get_object_or_404(AirregiSync, pk=pk)
    try:
        data = services.export_airregi(sync)
    except (airregi.AirregiCsvError, services.InventoryError) as e:
        messages.error(request, f"ファイルを作れませんでした：{e}")
        return redirect("airregi_sync")
    resp = HttpResponse(data, content_type="text/csv")
    # Airレジの注意事項「ファイル名は変更しないでください」に合わせ、元と同じ名前で出す
    from urllib.parse import quote

    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(sync.filename)}"
    return resp


@admin_required
@require_POST
def airregi_uploaded(request, pk):
    sync = get_object_or_404(AirregiSync, pk=pk)
    try:
        if request.POST.get("answer") == "no":
            services.discard_export(sync)
            messages.info(request, "作ったファイルを取り消しました。未送信の記録は次回のファイルに入ります。")
        else:
            services.confirm_uploaded(sync)
            messages.success(request, "同期が完了しました。")
    except services.InventoryError as e:
        messages.error(request, str(e))
    return redirect("airregi_sync")


# ---------------------------------------------------------------- 商品


PRODUCT_CSV_HEADER = ["商品コード", "商品名", "ふりがな", "カテゴリ", "単位", "発注点", "日持ち日数", "いつもの保管場所", "メモ"]


@admin_required
def product_list(request):
    products = list(_products_with_stock(Product.objects.all()))
    return render(request, "inventory/product_list.html", {"products": products, "header": PRODUCT_CSV_HEADER})


@admin_required
def product_csv_template(request):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(PRODUCT_CSV_HEADER)
    for p in Product.objects.all():
        w.writerow(
            [p.code, p.name, p.kana, p.category, p.unit, p.reorder_point, p.shelf_life_days or "",
             p.default_location.name if p.default_location else "", p.memo]
        )
    resp = HttpResponse(buf.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="products.csv"'
    return resp


@admin_required
@require_POST
def product_csv_import(request):
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "ファイルを選んでください。")
        return redirect("product_list")
    raw = f.read()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        messages.error(request, "ファイルの文字コードを読み取れませんでした。")
        return redirect("product_list")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "商品コード" not in reader.fieldnames or "商品名" not in reader.fieldnames:
        messages.error(request, "1行目に「商品コード」「商品名」などの項目名が必要です。ひな形をダウンロードして使ってください。")
        return redirect("product_list")
    locations = {loc.name: loc for loc in Location.objects.all()}
    created = updated = 0
    errors = []

    def num(v, field, line):
        v = (v or "").strip()
        if not v:
            return None
        if not v.isdigit():
            errors.append(f"{line}行目：{field}は数字で入れてください。")
            return None
        return int(v)

    with transaction.atomic():
        for i, row in enumerate(reader, start=2):
            code = (row.get("商品コード") or "").strip()
            name = (row.get("商品名") or "").strip()
            if not code and not name:
                continue
            if not code or not name:
                errors.append(f"{i}行目：商品コードと商品名は必須です。")
                continue
            loc_name = (row.get("いつもの保管場所") or "").strip()
            if loc_name and loc_name not in locations:
                errors.append(f"{i}行目：保管場所「{loc_name}」が登録されていません。")
                continue
            values = {
                "name": name,
                "kana": (row.get("ふりがな") or "").strip(),
                "category": (row.get("カテゴリ") or "").strip(),
                "unit": (row.get("単位") or "").strip() or "個",
                "reorder_point": num(row.get("発注点"), "発注点", i) or 0,
                "shelf_life_days": num(row.get("日持ち日数"), "日持ち日数", i),
                "memo": (row.get("メモ") or "").strip(),
            }
            if loc_name:
                values["default_location"] = locations[loc_name]
            _, is_new = Product.objects.update_or_create(code=code, defaults=values)
            created += is_new
            updated += not is_new
        if errors:
            transaction.set_rollback(True)
    if errors:
        messages.error(request, "取り込めませんでした。" + " / ".join(errors[:10]))
    else:
        messages.success(request, f"商品を {created}件 追加、{updated}件 更新しました。")
    return redirect("product_list")
