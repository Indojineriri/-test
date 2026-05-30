# keiba — 競馬予想・可視化ツール

競馬（例: 日本ダービー）を題材に、データ収集 → 分析 → 可視化 → 示唆出し → 予想までを
段階的に作っていくツールです。

```
①過去のレースデータ収集  →  ②出走馬の成績収集  →  ③成績を可視化
        →  ④成績ベースの示唆出し（ML + 生成AI）  →  ⑤実際の予想
```

> ⚠️ このリポジトリのルートには別アプリ（PowerPoint/アジェンダ生成）が同居しています。
> 競馬ツールは `src/keiba/`（パッケージ本体）と `tests/`（テスト）に分離しています。

## いま実装済みの範囲（フェーズ1: ①②データ取得）

データの取得元として **netkeiba スクレイピング** を採用。設計の肝は
**「HTTP取得」と「HTMLパース」を完全分離**したことです。

```
src/keiba/
  schema.py                  正規化スキーマ（列定義・RaceContext・RUN_COLUMNS）★全層の共通語彙
  dataset.py                 ★runs 統合 + point-in-time 特徴量（出馬表→学習データの橋渡し）
  storage.py                 ローカル / GCS を切り替える Storage 抽象（Cloud Run 永続化）
  service.py                 取得→保存→参照の中核（Flask 非依存）
  collect/
    base.py                  DataSource 抽象インターフェース（取得元を差し替え可能に）
    netkeiba_client.py       HTTP 取得のみ（キャッシュ/レート制限/リトライ/文字コード）
    netkeiba_parse.py        HTML→DataFrame の純粋関数群（★ネットワーク非依存=テスト可能）
    netkeiba.py              上記2つを束ねる NetkeibaDataSource + race_id ビルダ
  web/app.py                 Flask アプリ（Cloud Run / WSGI: keiba.web.app:app）
  cli.py                     コマンドライン
tests/
  fixtures/                  netkeiba 構造を模した HTML（オフライン検証用）
  test_netkeiba_parse.py     パーサ & データソースの単体/結合テスト
  test_dataset.py            runs 統合・特徴量・リーク無しの検証
  test_web.py                Storage / service / Flask の結合テスト（オフライン）
docs/
  data-model.md              ★出馬表とは何か / 収集範囲 / 特徴量の作り方（設計の核）
deploy/keiba/                Cloud Run デプロイ（Dockerfile / cloudbuild.yaml / deploy.sh / README）
```

## Cloud Run で運用する

HTTP サービスとして Cloud Run にデプロイできます（取得データは GCS に永続化）。
詳細は **[deploy/keiba/README.md](../deploy/keiba/README.md)**。

```bash
./deploy/keiba/deploy.sh
# デプロイ後:
URL="$(gcloud run services describe keiba --region asia-northeast1 --format 'value(status.url)')"
curl "$URL/fetch?race_id=202605021211"   # 取得して GCS に保存
curl "$URL/races/202605021211/entries.csv"
```

### なぜこの分離なのか

- **パースはネットワーク不要でテストできる**: `netkeiba_parse.py` は HTML 文字列を
  受け取り DataFrame を返すだけ。`tests/fixtures/*.html` に対して単体テストでき、
  サイトの構造変更にもここだけ直せば追従できる。
- **取得元を差し替えられる**: `DataSource` を実装すれば netkeiba → JRA-VAN → CSV と
  乗り換え可能。下流（可視化・予想）のコードは一切変えなくてよい。
- **礼儀正しいスクレイピング**: `NetkeibaClient` がリクエスト間ウェイト・ディスク
  キャッシュ・指数バックオフ・正しい文字コード（db系は EUC-JP）を内蔵。

### ★ 出馬表(entries)は予測の中で何なのか（設計の核）

これは別文書 **[docs/data-model.md](docs/data-model.md)** に詳述。要点だけ:

- **出馬表 = 「結果がまだ確定していないレース結果テーブル」**。粒度は results と同じ
  「1行 = 1頭の1出走(run)」で、着順が未確定なだけ。よって両者は `runs` という
  1本の縦長テーブルに統合する（`is_target` フラグで区別、`finish_pos` は entries で NaN）。
- **出馬表の本当の役割は「18頭の horse_id を供給する起点」**。そこから
  各馬のキャリア → 各レースの全出走馬（同走馬込み）と2ホップ辿ることで、
  1レースの予想が数百〜数千 run の学習データに広がる。
- **「1年分のダービー結果」だけでは学習も予想もできない**: 18行・条件1種で変動ゼロ、
  しかも出走馬の“その時点の実力”が分からない。`tests/test_dataset.py::
  test_one_race_only_is_not_trainable` がこれを対比で証明している。
- **特徴量は point-in-time（リーク厳禁）**: 各 run の特徴量は「その馬の基準日より前の
  run だけ」から作る。学習(results)と推論(entries)で同一の特徴量生成器を通すことで
  train/serve skew を防ぐ。

実際に作られる様子を見るには:

```bash
python3 -m keiba.cli demo-dataset --entrants 18 --career 7
# 出馬表18頭 → 同走馬込み 300+ run の学習データ / 各馬 time line で pit_starts が
# 0,1,2,... と増え、特徴量が必ず“その行より前”から作られる（リーク無し）様子を表示
```

### netkeiba の race_id 体系

12桁 = `年(4) + 競馬場(2) + 開催回(2) + 開催日(2) + R(2)`。
ヘルパで条件から組み立てられます。

```bash
# 2024 日本ダービー = 東京・2回・12日目・11R
python3 -m keiba.cli race-id --year 2024 --track 東京 --kai 2 --day 12 --race 11
# -> 202405021211
```

## 使い方

```bash
python3 -m pip install -r requirements-keiba.txt
export PYTHONPATH=src
```

### オフラインで動く（この環境でもOK）

```bash
# ローカル HTML をパースして中身を確認
python3 -m keiba.cli parse-file --kind result --race-id 202405021211 \
    --file tests/fixtures/race_result.html

# テスト
python3 tests/test_netkeiba_parse.py
```

### ネットワークのある手元環境で動かす

```bash
# 対象レースの出馬表→各馬の過去成績→血統 をまとめて取得し CSV 化
python3 -m keiba.cli fetch --race-id 202405021211 --out data/derby2024 --wait 1.5

# 生成物: data/derby2024/{entries,results,races,horses}.csv, race_meta.json
```

`fetch` の流れ:
1. 出馬表ページから出走馬の `horse_id` を取得（②の対象確定）
2. 各馬の競走馬ページから過去レースの `race_id` を収集
3. 各レース結果ページをパースして履歴を構築（①）
4. 各馬の血統を抽出

## データスキーマ（正規化後）

`src/keiba/schema.py` に定義。主な列:

| テーブル | 単位 | 主な列 |
|---|---|---|
| `races` | 1レース | `race_id, date, race_name, track, surface, distance, direction, going, weather, grade, n_horses` |
| `results` | 完了レース×馬 | `race_id, horse_id, finish_pos, frame_no, horse_no, sex, age, impost, jockey, time_sec, passing, last_3f, odds, popularity, horse_weight, weight_diff, trainer` |
| `entries` | 対象レース×馬 | `race_id, horse_id, frame_no, horse_no, sex, age, impost, jockey, odds, popularity, horse_weight` |
| `horses` | 1頭 | `horse_id, horse_name, sex, birth_year, sire, dam, dam_sire, trainer` |
| **`runs`** | **1出走(run)** | **results + entries を縦持ち統合。`is_target` で対象/過去を区別、`finish_pos` は entries で NaN。`dataset.py` が生成。これが ML の入力テーブル** |

## 法務・運用上の注意

- **スクレイピング前に robots.txt と利用規約を必ず確認**。私的・研究目的の範囲で、
  アクセス頻度を抑えて利用してください。`NetkeibaClient` は既定で 1.5 秒の
  ウェイトとキャッシュを入れています。
- 本ツールは学習・分析目的です。馬券購入は自己責任で。

## ロードマップ

- [x] **フェーズ1: ①②データ取得**（netkeiba／HTTPとパースの分離／オフラインテスト）
- [x] **フェーズ1.5: データモデル設計**（runs 統合 / point-in-time 特徴量 / リーク防止
      ／「出馬表とは何か・1レースをデータセットに広げる収集範囲」を docs と動くコードで確定）
- [x] **フェーズ1.8: Cloud Run 化**（Flask サービス + GCS 永続化 / Storage 抽象 /
      deploy.sh。詳細は [deploy/keiba/README.md](../deploy/keiba/README.md)）
- [x] **フェーズ2: ④分析**（健全性診断・出走馬成績・脚質・賞金・グレード・間隔）
- [x] **フェーズ3: ⑤予想（ML）**（`keiba predict`：過去ダービーで学習→複勝確率ランキング
      ＋◎○▲印。`keiba backtest`：leave-one-out で的中率集計。リーク防止済み）
- [x] **フェーズ4: ④⑤ 生成AI（2段エージェント）**（`keiba genai-predict`：過去ダービーから
      Claude が示唆を導出→その示唆で今年の出走馬を評価。Opus 4.8 / 構造化出力 / プロンプトキャッシュ）
- [ ] フェーズ5: ③可視化（matplotlib で脚質分布・賞金順などのグラフ PNG）
- [ ] フェーズ6: 回収率ベースの評価（オッズと結びつけた期待値）

### ④⑤ 生成AI予想の使い方

過去ダービーから Claude が「好走馬の傾向」を導き、その示唆を今年の出走馬に当てはめて予想します。

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # 必須
python3 -m keiba.cli genai-predict --race-id 202605021211 --store gs://keiba_shohei/keiba
```

- 2段構成: ①過去ダービー(各馬のレース前指標→実着順)から示唆を導出 → ②示唆を今年の出走馬に適用。
- モデルは `claude-opus-4-8`、adaptive thinking、構造化出力(pydantic)、過去データはプロンプトキャッシュ。
- ML(`predict`)が数値スコア、生成AI(`genai-predict`)が言語化された根拠つき予想。併用すると解釈しやすい。

### ⑤予想（ML）の使い方

```bash
# 過去ダービー全年で学習し、2026 を複勝確率で予想（既知表から学習年を自動選択）
python3 -m keiba.cli predict --race-id 202605021211 --store gs://keiba_shohei/keiba --show-coef

# 過去ダービーで的中率をバックテスト（leave-one-out）
python3 -m keiba.cli backtest --years 2016-2024 --store gs://keiba_shohei/keiba
```

- 特徴量は point-in-time（その馬の前走まで）なのでリークしない。
- ラベルは複勝(3着内)の二値。ロジスティック回帰ベースライン（係数で根拠が見える）。
- backtest は各年を「その年以外で学習」して評価するので、未来情報を使わない。
