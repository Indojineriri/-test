# リサーチ資料作成アプリ

テーマを入力すると、Claude の Web 検索ツール + arXiv API でリサーチを行い、結果を CSV / PPT 形式でダウンロードできる Streamlit アプリ。

## 利用フロー

1. **テーマ入力**（例：「直近の VLA に触覚センサを用いた事例をピックアップしてください」）
2. **リサーチ実行** — Claude の `web_search` + arXiv の最新論文をコンテキストとして渡し、事例を抽出
3. **構造化** — 自由記述のリサーチ結果を `Case` スキーマに沿った JSON へ変換
4. **出力**
   - 事例リストを **CSV** でダウンロード（Excel 互換、UTF-8 BOM 付き）
   - 各事例を **PPT** 1 スライドずつ、添付テンプレート (`templates/case_template.pptx`) のレイアウトで出力

## セットアップ（ローカル）

```bash
cd research-app
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run app.py
```

API キーはサイドバーから入力するか、環境変数 `ANTHROPIC_API_KEY` を設定する。

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
