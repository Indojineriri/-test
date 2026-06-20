# 🀄 麻雀 日程調整ツール

候補枠は **金曜の夜 / 土・日の昼・夜**。月カレンダーの各マスで出欠
（○参加 / △未定 / ×欠席）をプルダウンから選ぶだけで日程調整できるツールです。

## 特長
- **月カレンダー表示**：実際のカレンダーのマス目に、その日の候補枠（金=夜、
  土日=昼・夜）が並びます。金=緑 / 土=青 / 日=赤で色分け、今日は強調表示。
- **名前は手入力**：サイドバーにお名前を入力するだけで使えます。
- **出欠はプルダウンで選ぶだけ**：各マスの枠ごとに「○参加 / △未定 / × 欠席 /
  未回答」を選びます。選んだ瞬間に保存されます。
- **「詳細」ボタンで参加者を確認**：押すとカレンダー下にその枠の参加者が
  ○/△/× 別に表示されます。
- **見やすい集計**：各枠に `○n △n ×n` を色付き表示。成立枠は「✅ 成立」、
  もう少しの枠は「あとX人」と表示します。
- **成立候補の一覧**：○が必要人数（既定4人）以上そろった枠を下部にまとめて表示。
- **同時更新に強い**：保存時に最新状態を読み直してから自分の回答だけ反映するため、
  複数人が同時に入力しても他の人の回答が消えません。
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
