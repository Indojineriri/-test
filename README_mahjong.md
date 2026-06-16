# 🀄 麻雀 日程調整ツール

毎週の土日 × 昼/夜 の固定枠に対して、メンバーが ○/△/× を入力するだけで日程を
調整できる、月めくりカレンダー形式のツールです。

## 特長
- **月表示カレンダー**：土日のマスにだけ「昼」「夜」の枠が出ます。
- **タップで回答**：枠のボタンを押すたびに `－ → ○ → △ → ×` と切り替わります。
- **名前は手入力**：サイドバーにお名前を入力して使います。**メールアドレスは任意**で、
  入れておくと他のメンバーが成立候補からあなたに直接連絡できます（mailto リンク）。
- **自動ハイライト**：○が必要人数（既定4人）以上そろった枠を緑＋✅で表示し、
  下部に「成立候補」として参加者一覧つきで表示します。
- **設定不要で動く**：GCSバケットを設定すれば全員で共有、未設定ならローカルの
  `data/mahjong.json` に保存します。

## 起動方法（ローカル）

```bash
pip install -r requirements.txt
streamlit run mahjong_app.py
```

ブラウザが開いたら、サイドバーにお名前（必要ならメールも）を入力して使い始めます。

## Cloud Run へデプロイ（みんなで共有）

回答は GCS の **`test_reseach` バケットの `mahjong/` フォルダ**
（`gs://test_reseach/mahjong/availability.json`）に保存され、全員で同じ
カレンダーを共有できます。専用スクリプトでデプロイできます。

```bash
./deploy_mahjong.sh
```

- サービス名は `mahjong-scheduler`（`SERVICE` で変更可）。
- バケット／フォルダは環境変数で上書きできます：
  `GCS_BUCKET`（既定 `test_reseach`）、`GCS_PREFIX`（既定 `mahjong`）。
- このアプリは Anthropic API を使わないため、API キーは不要です。
- 1つの Docker イメージで会議ツール（`app.py`）と麻雀ツール（`mahjong_app.py`）の
  両方に対応します。どちらを起動するかは環境変数 `APP_FILE` で切り替えます。

> ⚠️ `deploy_mahjong.sh` は `--allow-unauthenticated` で公開します。URL を知る人は
> 誰でも閲覧・編集できます。限定したい場合は `--no-allow-unauthenticated` に変えて
> 特定ユーザーに `run.invoker` を付与してください。

## データの保存場所
- GCS 設定あり：`gs://<GCS_BUCKET>/<GCS_PREFIX>/availability.json`
- GCS 設定なし：`./data/mahjong.json`（`.gitignore` 済み）

## 環境変数まとめ
| 変数 | 既定値 | 用途 |
|---|---|---|
| `APP_FILE` | `app.py` | Cloud Run で起動する Streamlit アプリ |
| `GCS_BUCKET` | （未設定） | 保存先バケット。未設定ならローカル保存 |
| `MAHJONG_GCS_PREFIX` | `mahjong` | バケット内の保存フォルダ |
| `MAHJONG_LOCAL_PATH` | `data/mahjong.json` | ローカル保存時のパス |
