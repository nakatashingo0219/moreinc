# お花在庫管理システム｜株式会社きむらフローリスト（Phase 1）

株式会社きむらフローリスト向けの在庫管理システムです。主要商品の「何が・どこに・何個あるか」を管理し、Airレジの在庫数とCSVで同期します。

- 要件定義書：[docs/requirements.md](docs/requirements.md)
- 言語・フレームワーク：Python 3.12 以上、Django 5.2、SQLite
- 画面：ブラウザで使うWebアプリ。タブレット・スマートフォン・パソコンに対応

## Phase 1 でできること

| 機能 | 内容 | 使える人 |
|---|---|---|
| 在庫を見る | 商品ごとの合計と、保管場所ごとの内訳。ひらがな・カタカナどちらでも検索でき、在庫が少ない商品は色で分かる | 全員 |
| 入荷・廃棄・移動・数の修正・店外販売 | 商品を選んで、数を入れて、確定するだけ。記録した直後に「取り消す」ボタンが出る | 全員 |
| 記録の履歴と取り消し | いつ・誰が・何をしたかを残す。間違えた記録は取り消せる | 全員 |
| 棚卸し | 場所ごとに数えた数を入力し、途中保存もできる。差を確認してから確定する。結果はCSVで出力できる | 入力は全員、開始・確定は店長 |
| Airレジ同期 | Airレジの「在庫一括編集CSV」を取り込んで販売数を反映し、入荷・廃棄・棚卸しの増減をAirレジ用のCSVにして返す | 店長 |
| 商品の登録 | Airレジのファイルから選んで登録、CSVで一括登録・更新、1件ずつ登録 | 店長 |
| 通知 | 発注点以下の商品、在庫がマイナスの商品、日持ちの近い商品、Airレジへの未送信をホーム画面に表示 | 全員 |
| 設定 | 保管場所・ユーザー・商品の編集（Django の管理画面） | 店長 |

画面のヘッダーとログイン画面には、きむらフローリストのロゴ（公式サイトの画像）、システム名、会社名を表示します。表示名は環境変数 `APP_NAME`・`COMPANY_NAME` で変えられます。ロゴの画像は `inventory/static/inventory/logo.png` です。

価格は扱わず、数量だけを管理します。アレンジ・花束の構成、写真、ダッシュボードは Phase 1 では作っていません（要件定義書 3章）。

---

## 1. 試しに動かす（パソコン1台で）

Python 3.12 以上が入っているパソコンで、このフォルダ（`flower-inventory`）を開いて実行します。

Mac / Linux:

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

Windows（PowerShell）では、代わりに次を実行します。

```bash
py -m venv .venv
```

```bash
.venv\Scripts\Activate.ps1
```

ここから先は共通です。

```bash
pip install -r requirements.txt
```

```bash
python manage.py migrate
```

```bash
python manage.py setup_shop --demo-users
```

```bash
python manage.py runserver
```

ブラウザで http://127.0.0.1:8000 を開き、次のどちらかでログインします（動作確認専用です）。

| ユーザー名 | パスワード | 権限 |
|---|---|---|
| `tencho` | `demo1234` | 店長（すべての画面） |
| `staff` | `demo1234` | スタッフ（Airレジ同期・商品・設定以外） |

**Airレジ同期のお試し**：`tencho` でログインし、「Airレジ同期」で `samples/airregi_stock_sample.csv` を取り込みます。サンプルの20品目が商品コードでひも付き、在庫数が入ります。

同じネットワークのタブレットから試すときは、`python manage.py runserver 0.0.0.0:8000` で起動します。そのうえで、環境変数 `DJANGO_ALLOWED_HOSTS` にパソコンのIPアドレスを加えてください（例：`localhost,127.0.0.1,192.168.1.10`）。

## 2. 本番で使う

### 2.1 置き場所

店舗の外からも使え、バックアップもしやすいので、クラウドに置くことを推奨します。データベースはSQLiteなので、**サーバーを作り直しても消えない保存場所（永続ディスク）** が必要です。

- 例：Render（Web Service と Persistent Disk）、Railway（Volume）、さくらのVPS・ConoHa VPS など
- 永続ディスクが使えないサービスでは、データベースをPostgreSQLに切り替えてください（`config/settings.py` の `DATABASES` を変更）。

### 2.2 環境変数

| 名前 | 内容 | 例 |
|---|---|---|
| `DJANGO_SECRET_KEY` | 必須。長いランダムな文字列 | `python -c "import secrets;print(secrets.token_urlsafe(50))"` の結果 |
| `DJANGO_DEBUG` | 本番は必ず `0` | `0` |
| `DJANGO_ALLOWED_HOSTS` | 公開するドメイン | `zaiko.example.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://` を付けたドメイン | `https://zaiko.example.com` |
| `DJANGO_DB_PATH` | SQLiteファイルの場所（永続ディスク上） | `/var/data/db.sqlite3` |

### 2.3 初回のセットアップ

```bash
pip install -r requirements.txt
```

```bash
python manage.py migrate
```

```bash
python manage.py collectstatic --noinput
```

保管場所とサンプル20品目を登録します。実際の商品リストが届いている場合は、`--no-products` を付けて保管場所だけを登録し、商品はAirレジ同期またはCSVで登録してください。

```bash
python manage.py setup_shop
```

店長のアカウントを作ります（ユーザー名とパスワードを聞かれます）。

```bash
python manage.py createsuperuser
```

起動コマンドの例です（`$PORT` はサービスが指定するポート）。

```bash
gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 2
```

スタッフのアカウントは、店長でログインして「設定」→「ユーザー」から追加します。スタッフには「スタッフ権限」を付けないでください（付けると店長と同じ画面が使えます）。

### 2.4 Airレジ側の準備

1. Airレジの「レジ基本設定」で「在庫管理」をオンにする。
2. このシステムで管理する商品を、Airレジでも「管理対象」にする（在庫管理画面 → 管理対象・在庫通知一括編集）。
3. Airレジの「商品コード」に、このシステムと同じ商品コード（例 `P001`）を入れておくと、最初の取り込みで自動的にひも付きます。商品コードがなくても、取り込み後に「まだ登録していない商品」から選んで登録できます。

### 2.5 バックアップ

`DJANGO_DB_PATH` のファイル1つをコピーすればバックアップになります。毎日、別の場所にコピーしてください（30日分を残すのが目安）。Linux の例：

```bash
sqlite3 /var/data/db.sqlite3 ".backup '/var/backup/db-$(date +%Y%m%d).sqlite3'"
```

## 3. 毎日の使い方

- **スタッフ**：入荷したら「入荷」、傷んで捨てたら「廃棄」、置き場所を変えたら「移動」を記録します。Airレジで会計した販売は記録しなくて構いません（同期で反映されます）。
- **店長（1日1回、閉店後、パソコンで）**：「Airレジ同期」の4ステップを行います。
  1. Airレジ バックオフィス：在庫管理 →「在庫数をCSVで編集する」→ 編集用ファイルをダウンロード
  2. このシステム：そのファイルを取り込む（Airレジで売れた数が在庫に反映される）
  3. このシステム：「ファイルを作ってダウンロード」（元と同じファイル名で出る。名前は変えない）
  4. Airレジ：「ファイルで一括編集」で **「現在の在庫数に足し引きする」** を選んでアップロードし、このシステムで「アップロードした（完了）」を押す

## 4. Airレジ連携の仕組み（開発者向け）

- Airレジの在庫一括編集CSVの形式は、1行目がバージョン（`v200`）、2行目が項目名（13列）、3行目以降がデータです。処理は `inventory/airregi.py` にまとめています。
- 書き出すときは、**取り込んだファイルの文字コード・改行・引用符・行の並びをそのまま保ち**、「更新在庫数」「更新種別」「更新メモ」の3列だけを書き換えます。変更のない行は3列とも空欄にします。
- Airレジには常に「足し引き」（差分）で返します。送る数は「このシステムの合計 − 取り込み時のAirレジの在庫数」です。こうすると、取り込みからアップロードまでの間にAirレジで売れた分を消さずに済み、両者の食い違いも自然に解消されます。
- 「アップロードした」を押すまで、送った記録は「アップロード待ち」になります。押し忘れたまま次のファイルを取り込もうとすると、アップロードしたかどうかを聞きます（二重送信の防止）。
- 更新メモは30文字以内に切り、Airレジで使えない機種依存文字（半角カナ、①、㈱ など）を取り除きます。

**実ファイルでまだ確認していない点**（Airレジのサンプルファイルが届いたら確認する）：

1. 文字コード。UTF-8 と Shift_JIS のどちらでも読み込めるようにしてあります。
2. ファイル名の付き方
3. 「更新在庫数」が空欄の行を、Airレジが「変更なし」として扱うか

## 5. フォルダ構成

```
flower-inventory/
├── config/                 Django の設定
├── inventory/
│   ├── airregi.py          Airレジ CSV の読み書き（Django に依存しない）
│   ├── services.py         在庫を動かす処理（入出庫・同期・棚卸し・通知）
│   ├── models.py           データの構造
│   ├── views.py            画面
│   ├── templates/          画面のHTML
│   ├── static/             CSS・JavaScript
│   ├── management/commands/setup_shop.py   初期データの登録
│   └── tests/              自動テスト
├── samples/airregi_stock_sample.csv        Airレジ CSV のサンプル（仮の20品目、Shift_JIS）
└── docs/requirements.md    要件定義書
```

## 6. テスト

```bash
python manage.py test inventory
```
