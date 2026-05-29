# 🎲 ボードゲーム図鑑（Board Game Catalog）

ボードゲームを「探す → ルールを読む → みんなで遊ぶ」を一気通貫でサポートする Web アプリです。

- **一覧・絞り込み**: ゲームのタイプ（デッキ構築 / 正体隠匿 / タイル配置 など）やプレイ人数で絞り込み・並べ替え。
- **🔮 AI検索（自然言語）**: 「カタンと似た交渉ゲーム」「初心者と短時間で遊べる協力ゲーム」のような
  曖昧な文章を Claude が解釈し、収録ゲームから推薦理由付きでおすすめを提示（`ANTHROPIC_API_KEY` 必要）。
- **ルール詳細**: 各ゲームの「🎯目的 / 👤登場人物・役職 / 🔄手順 / 🏁終了条件」を構造化して表示。
- **オンラインで試す**: 実際に遊べるサイト（Colonist.io, Dominion Online, Board Game Arena など）へのリンク。
- **🖼️ ゲーム画像**: 各ゲームに画像URL（`image_url`）を設定でき、一覧カード・詳細に表示。未設定時は 🎲 プレースホルダー。
- **⭐ お気に入り / ✅ プレイ済み**: ログイン不要。ブラウザ単位（Cookieの匿名ID）でお気に入り・プレイ済みを記録し、
  詳細ページでは **5段階評価と感想メモ** も保存可能。一覧の上部タブで「お気に入りのみ／プレイ済みのみ」に絞り込めます。
- **遊ぶ会・参加管理**: ゲーム会を作成し、遊ぶゲームと参加者を登録。人数がそのゲームの対応人数に合っているかを自動チェック。
- **ゲーム追加**: フォームから手動追加（画像URLも設定可）。`ANTHROPIC_API_KEY` を設定すれば「🤖 AIでルール下書きを生成」も使えます。

> 収録数は **69 タイトル**（2人用名作・最新人気作・パーティ系まで網羅）。AI検索・AIルール生成は
> `ANTHROPIC_API_KEY` 未設定でも通常のキーワード検索・手動追加にフォールバックして動作します。

## 技術構成

- Python / Flask + Flask-SQLAlchemy
- SQLite（`boardgames.db`、初回起動時に自動生成）
- サーバーサイドテンプレート（Jinja2）＋ 素の CSS（ビルド不要）

## セットアップ

```bash
cd boardgame
python -m venv .venv && source .venv/bin/activate   # 任意
pip install -r requirements.txt

python seed.py     # data/games.json を DB に投入（再実行で更新・追加）
python app.py      # http://127.0.0.1:5000
```

## Cloud Run へのデプロイ

`boardgame/` ディレクトリで `deploy.sh` を実行します（`gcloud` 認証済みであること）。

```bash
cd boardgame
gcloud auth login            # 初回のみ
./deploy.sh                  # ビルド → Cloud Run へデプロイ → 公開URLを表示
```

- 本番は **gunicorn**（`app:app`）で起動します。Cloud Run が注入する `$PORT` に bind します。
- 起動時に DB が空なら `data/games.json` を**自動投入**するので、`seed.py` の手動実行は不要です。
- `ANTHROPIC_API_KEY` は**任意**です。Secret Manager に `anthropic-api-key` があれば
  自動で AI ルール生成に使われ、無ければカタログ機能のみで動作します
  （新規に作りたい場合は `CREATE_SECRET=1 ./deploy.sh`）。
- プロジェクト/サービス名などは環境変数で上書きできます:
  `PROJECT_ID=your-project SERVICE=boardgame-staging ./deploy.sh`

### データの永続性（GCS バックエンド）

Cloud Run のディスクは揮発性ですが、本アプリは **SQLite の DB ファイルを GCS に保存**
することで永続化します（既存 meeting-support アプリと同じバケット・認証を流用）。

- 起動時に `gs://$GCS_BUCKET/$GCS_DB_BLOB` から DB をダウンロード。
- 書き込み（POST）のたびに DB ファイルを GCS へアップロード。
- DB が無い初回は `games.json` を投入してから GCS に保存。

`deploy.sh` が以下の環境変数を設定します（ローカルで `GCS_BUCKET` 未設定なら
従来どおりローカルの `boardgames.db` を使います）:

| 環境変数 | 既定値 | 説明 |
|---|---|---|
| `GCS_BUCKET` | `test_reseach` | DB を置くバケット |
| `GCS_DB_BLOB` | `boardgame/boardgames.db` | バケット内のパス |

> ⚠️ **単一インスタンス前提**: GCS はファイル丸ごと置換のため、複数インスタンスが
> 同時に書くと上書き競合（last-write-wins）が起きます。`deploy.sh` は
> `--max-instances 1` で固定しています。書き込み頻度が上がる／本格運用する場合は
> **Cloud SQL（PostgreSQL）** へ移行し、環境変数 `DATABASE_URL` を設定してください
> （設定時はそちらが優先され、GCS 同期は無効になります）。

> 公開範囲の注意: `deploy.sh` は `--allow-unauthenticated`（URL を知れば誰でもアクセス可）です。
> 社内限定にするなら `--no-allow-unauthenticated` に変え、IAP もしくは `run.invoker` 権限で制限してください。

## ルール解説リンク（rules_url）の一括設定

ゲーム名→URL の対応表（JSON）を用意して `set_links.py` を実行すると、各ゲームの
`rules_url`（詳細ページの「🔗 詳しいルール解説を見る」リンク）を一括設定できます。

```bash
# links.json 例: { "カタン": "https://...", "ドミニオン": "https://..." }
python set_links.py links.json          # rules_url を設定
python set_links.py links.json --field image_url   # 画像URLにも使える
```

設定後はローカルなら `python seed.py`、Cloud Run なら再デプロイで反映されます
（rules_url が空のゲームにのみ起動時 backfill されます）。

## ユーザー投稿写真

各ゲームの詳細ページから、プレイ風景やコンポーネントの**写真を誰でも投稿**できます
（ログイン不要・ニックネーム任意・最大8MB・JPEG/PNG/WebP/GIF）。投稿者本人（同じブラウザ）は
自分の写真を削除できます。

- `GCS_BUCKET` が設定されていれば写真は `gs://$GCS_BUCKET/$GCS_PHOTO_PREFIX/...` に保存され、
  公開URL（`https://storage.googleapis.com/...`）で表示されます。
  → **バケットを公開読み取り可能**にしておく必要があります（例: `allUsers` に
  `roles/storage.objectViewer` を付与、または該当プレフィックスを公開設定）。
- `GCS_BUCKET` 未設定（ローカル開発）では `static/uploads/` に保存されます（gitignore 済み）。

## ゲーム画像の取り込み（Wikipedia / BGG ページから解決）

`image_pages.json`（ゲーム名→Wikipedia/BGGの**記事ページURL**）をもとに、各ページの代表画像の
**直リンク**を解決して `image_url` に書き込みます。記事ページURLはそのままでは `<img>` に使えない
ため、API/メタタグから画像URLを取り出す必要があります。**外部ネットワークに到達できる環境**で実行してください。

```bash
cd boardgame
pip install requests
python fetch_page_images.py             # image_pages.json から画像URLを解決
python fetch_page_images.py --force     # 既存のimage_urlも上書き
python fetch_page_images.py --only カタン ドミニオン
```

（各ゲームが持つ BGG ページURL から取得する旧スクリプト `fetch_images.py` も利用できます。）

```bash
python fetch_images.py                  # bgg_url から image_url を一括取得
python fetch_images.py --force
python fetch_images.py --only カタン ドミニオン

取得後、画像URLを反映させます:

- **ローカル / GCSなし**: `python seed.py` を再実行（名前一致で `image_url` を更新）。
- **Cloud Run（GCS運用）**: すでにDBがある場合は自動再シードされません。次のいずれかを実施してください。
  1. ローカルで `fetch_images.py` → `seed.py` を実行して GCS の DB を更新してから再デプロイ、または
  2. `image_url` を更新した `games.json` を push 後、GCS上の DB を一度削除（`gs://$GCS_BUCKET/$GCS_DB_BLOB`）して再起動 → 自動再シード。

> なぜスクリプト方式か: 画像は閲覧者のブラウザが直接読み込むため、URLさえ正確なら表示されます。
> 推測でURLを埋めると画像切れの恐れがあるため、BGG公式APIから正しいURLを取得する方式にしています。
> BGGへの礼儀として、リクエスト間に約2秒の待機を入れています。

## 収録データについて

`data/games.json` に、カタン・ニムト・カルカソンヌ・ナショナルエコノミー・ドミニオン・
レジスタンス・マスカレイド・パンデミック・モダンアート を含む人気タイトルを収録しています。

> 数百タイトルへ増やす場合は、`data/games.json` に追記するか、画面の「＋ゲーム追加」から
> 登録できます。`ANTHROPIC_API_KEY` を設定するとゲーム名から AI がルールの下書きを生成するので、
> 効率よくカタログを拡充できます（生成内容は必ず確認・修正してください）。

## ディレクトリ構成

```
boardgame/
├── app.py            # Flask アプリ本体・ルーティング
├── models.py         # Game / PlaySession / Participant モデル
├── ai_rules.py       # (任意) AI によるルール下書き生成
├── seed.py           # games.json を DB へ投入
├── data/games.json   # ゲームデータ
├── templates/        # Jinja2 テンプレート
└── static/style.css  # スタイル
```
