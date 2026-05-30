# 実行手順：GCS(`keiba_shohei`)に取得・保存して分析する

データ保存先は GCS バケット **`keiba_shohei`**（Cloud Run と Workbench の共有点）。
保存先 URI は `gs://keiba_shohei/keiba` を使う。

> なぜ GCS か: Cloud Run と Workbench は別マシンなので、両者が同じデータを見るには
> 共有ストレージ（GCS）が要る。VertexAI は計算プラットフォームで保存先ではない
> （データセットも内部的には GCS を参照する）。

---

## 0. 前提（Workbench `keiba-instance` で1回だけ）

```bash
cd ~/-test
git fetch origin
git checkout claude/affectionate-davinci-JSOUS     # 未切替なら
git pull origin claude/affectionate-davinci-JSOUS

python3 -m pip install -r requirements-keiba.txt
export PYTHONPATH=src

# GCS への読み書き認証（Workbench の SA に権限があれば不要なことが多い）
# 通常は Workbench のサービスアカウントで自動認証される。失敗したら:
#   gcloud auth application-default login
```

権限の確認（書ければOK）:

```bash
echo ok | gcloud storage cp - gs://keiba_shohei/keiba/_perm_check.txt && \
gcloud storage rm gs://keiba_shohei/keiba/_perm_check.txt && echo "write OK"
```

---

## 1. 取得して GCS に保存（手元=Workbench の回線で実行）

```bash
export PYTHONPATH=src
# 2026 日本ダービー
python3 -m keiba.cli fetch --race-id 202605021211 \
    --out gs://keiba_shohei/keiba --wait 1.5
```

保存結果:

```
gs://keiba_shohei/keiba/
  races/202605021211/
    entries.csv  results.csv  races.csv  horses.csv  meta.json
  cache/                      # 取得した生 HTML（再取得回避）
```

別のレースも同様に `--race-id` を変えるだけ（バケットは共有、レースごとに
`races/<race_id>/` に分かれる）。

---

## 2. 分析（GCS のデータを直接読む。取得しない）

```bash
python3 -m keiba.cli analyze --race-id 202605021211 \
    --store gs://keiba_shohei/keiba
```

- まず**データ健全性診断**（各馬の過去出走数・取りこぼし有無）
- 次に**出走馬の成績分析**（レース時点の実力。リーク無し）
- `--horse <horse_id>` で1頭の戦績を深掘り
- `--out-csv path.csv` で分析結果を保存

---

## 3. Cloud Run で参照（任意。デプロイ済みの場合）

Cloud Run は `KEIBA_STORAGE_URI=gs://keiba_shohei/keiba` を見て**参照専用**で動く
（取得はしない）。デプロイは:

```bash
./deploy/keiba/deploy.sh        # BUCKET 既定が keiba_shohei
```

参照:

```bash
URL="$(gcloud run services describe keiba --region asia-northeast1 \
       --format 'value(status.url)')"
curl "$URL/races"
curl "$URL/races/202605021211"
curl "$URL/races/202605021211/entries.csv"
```

---

## Jupyter から直接さわる場合（GCS のデータを DataFrame に）

```python
import sys; sys.path.insert(0, "src")
from keiba.storage import Storage
from keiba import service, analyze as A

store = Storage.from_uri("gs://keiba_shohei/keiba")
ctx = service.load_context("202605021211", store)   # 取得済みCSVを読み戻す

print(A.format_coverage(A.diagnose_coverage(ctx)))   # 健全性診断
view = A.analyze_entrants(ctx)                        # 出走馬の成績分析(DataFrame)
view   # ノートブックにそのまま表示
```

---

## トラブルシュート

| 症状 | 原因と対処 |
|---|---|
| `fetch` で 403 | netkeiba に IP ブロックされている。Workbench の回線でも稀に起きる。時間を空ける/別回線。Cloud Run からは不可（設計どおり手元で実行）。 |
| 複勝率や上り3Fが全部 `-`/NaN | 結果ページのパース崩れ。実HTMLとセレクタのズレ。`parse-file` で実HTMLを確認し `netkeiba_parse.py` を調整。 |
| 各馬の出走数が1〜2戦に偏る | 取りこぼしの疑い。`analyze` の診断と `meta.json` の counts を確認。 |
| GCS 書き込みで 403/Permission | Workbench SA に `roles/storage.objectAdmin` が必要。`gcloud auth application-default login` も試す。 |
