# 🎲 ボードゲーム図鑑（Board Game Catalog）

ボードゲームを「探す → ルールを読む → みんなで遊ぶ」を一気通貫でサポートする Web アプリです。

- **一覧・絞り込み**: ゲームのタイプ（デッキ構築 / 正体隠匿 / タイル配置 など）やプレイ人数で絞り込み・並べ替え。
- **🔮 AI検索（自然言語）**: 「カタンと似た交渉ゲーム」「初心者と短時間で遊べる協力ゲーム」のような
  曖昧な文章を Claude が解釈し、収録ゲームから推薦理由付きでおすすめを提示（`ANTHROPIC_API_KEY` 必要）。
- **ルール詳細**: 各ゲームの「🎯目的 / 👤登場人物・役職 / 🔄手順 / 🏁終了条件」を構造化して表示。
- **オンラインで試す**: 実際に遊べるサイト（Colonist.io, Dominion Online, Board Game Arena など）へのリンク。
- **遊ぶ会・参加管理**: ゲーム会を作成し、遊ぶゲームと参加者を登録。人数がそのゲームの対応人数に合っているかを自動チェック。
- **ゲーム追加**: フォームから手動追加。`ANTHROPIC_API_KEY` を設定すれば「🤖 AIでルール下書きを生成」も使えます。

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
