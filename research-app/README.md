# リサーチ資料作成アプリ

テーマを入力すると、Claude の Web 検索ツール + arXiv API でリサーチを行い、結果を CSV / PPT 形式でダウンロードできる Streamlit アプリ。

## 利用フロー

1. **ユーザー情報の入力** — サイドバーで名前とメールアドレスを設定（履歴を残すのに必須）
2. **テーマ入力**（例：「直近の VLA に触覚センサを用いた事例をピックアップしてください」）
3. **リサーチ実行**（モード選択）
   - **標準**: 1 リクエストで `web_search` + arXiv コンテキストを使って事例を抽出
   - **ディープリサーチ**: エージェントループで `web_search` / `fetch_url` / `arxiv_search` を多段に呼び、ページ本文の読み込み・裏取りを繰り返す（深く調べる代わりに時間と費用が増える）
4. **構造化** — 結果を `Case` スキーマに沿った JSON に変換（ディープモードは直接 JSON を提出）
5. **自動保存** — 完了時に GCS に履歴を保存（名前・メール必須）
6. **出力**
   - 事例リストを **CSV** でダウンロード（Excel 互換、UTF-8 BOM 付き）
   - 各事例を **PPT** 1 スライドずつ、添付テンプレート (`templates/case_template.pptx`) のレイアウトで出力
7. **履歴閲覧** — 「📚 履歴」タブで他のメンバーが行ったリサーチを検索・閲覧、メール窓口からコンタクト

## モード比較

| 項目 | 標準 | ディープリサーチ |
| --- | --- | --- |
| API リクエスト数 | 2（リサーチ + 構造化） | エージェントループ（〜10-20 ターン） |
| 所要時間 | 1〜2 分 | 5〜15 分 |
| トークンコスト | 小 | 大（標準の 3〜10 倍） |
| 深さ | 検索結果のスニペット中心 | 一次ソースの本文を実際に読む |
| 進捗表示 | スピナーのみ | ステップごとに live 表示 |

## セットアップ（ローカル）

```bash
cd research-app
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
# 履歴を GCS に保存したい場合（未設定なら /tmp/research-history にローカル保存）
export GCS_BUCKET=your-bucket
streamlit run app.py
```

API キーはサイドバーから入力するか、環境変数 `ANTHROPIC_API_KEY` を設定する。

## 履歴の保存先

- `GCS_BUCKET` 環境変数を設定すると `gs://<bucket>/research-history/` 配下に JSON で蓄積される
- 未設定の場合は `/tmp/research-history/` にローカル保存（Cloud Run では再起動で消える）
- 認証は Application Default Credentials を使用（ローカルなら `gcloud auth application-default login`、Cloud Run なら自動）

## デプロイ（Cloud Run）

```bash
cd research-app
./deploy.sh
```

初回実行時のみ Anthropic API キーを入力すると Secret Manager に保存される。
`gcloud auth login` 済みで、`PROJECT_ID` のデフォルト (`clean-pen-422206-d7`)
を変えたい場合は環境変数で上書きする：

```bash
PROJECT_ID=your-project SERVICE=research-app-staging ./deploy.sh
```

デプロイ後に表示される Cloud Run URL でブラウザから利用可能。
社内限定にしたい場合は `deploy.sh` の `--allow-unauthenticated` を
`--no-allow-unauthenticated` に変更し、IAP または `run.invoker` の付与で制限する。

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `app.py` | Streamlit UI |
| `research.py` | Claude API でのリサーチ + 構造化 |
| `arxiv_search.py` | arXiv API クライアント |
| `scraper.py` | 事例ページから og:image を取得 |
| `csv_export.py` | 事例リスト → CSV |
| `ppt_export.py` | 事例リスト → PPT（テンプレート差し込み） |
| `models.py` | `Case` / `CaseList` Pydantic スキーマ |
| `templates/case_template.pptx` | PPT 出力のテンプレート |

## PPT テンプレートの差し替え対象シェイプ

| シェイプ名 | 内容 |
| --- | --- |
| `タイトル 2` | 事例タイトル（`事例- {title} -`） |
| `テキスト ボックス 5` | 1〜2 文の要約 |
| `正方形/長方形 117` | 概要（箇条書き） |
| `正方形/長方形 118` | なぜ難しいのか（箇条書き） |
| `正方形/長方形 119` | 解決した技術的課題（箇条書き） |
| `図 10` | 代表画像（og:image を自動取得） |
| `テキスト ボックス 12` | リンクテキスト（ハイパーリンク付き） |

これらのシェイプ名はテンプレート編集時に維持すること。名前を変えると差し替えが効かなくなる。
