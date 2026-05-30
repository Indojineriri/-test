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
  schema.py                  正規化スキーマ（列定義・RaceContext）★全層の共通語彙
  collect/
    base.py                  DataSource 抽象インターフェース（取得元を差し替え可能に）
    netkeiba_client.py       HTTP 取得のみ（キャッシュ/レート制限/リトライ/文字コード）
    netkeiba_parse.py        HTML→DataFrame の純粋関数群（★ネットワーク非依存=テスト可能）
    netkeiba.py              上記2つを束ねる NetkeibaDataSource + race_id ビルダ
  cli.py                     コマンドライン
tests/
  fixtures/                  netkeiba 構造を模した HTML（オフライン検証用）
  test_netkeiba_parse.py     パーサ & データソースの単体/結合テスト
```

### なぜこの分離なのか

- **パースはネットワーク不要でテストできる**: `netkeiba_parse.py` は HTML 文字列を
  受け取り DataFrame を返すだけ。`tests/fixtures/*.html` に対して単体テストでき、
  サイトの構造変更にもここだけ直せば追従できる。
- **取得元を差し替えられる**: `DataSource` を実装すれば netkeiba → JRA-VAN → CSV と
  乗り換え可能。下流（可視化・予想）のコードは一切変えなくてよい。
- **礼儀正しいスクレイピング**: `NetkeibaClient` がリクエスト間ウェイト・ディスク
  キャッシュ・指数バックオフ・正しい文字コード（db系は EUC-JP）を内蔵。

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

## 法務・運用上の注意

- **スクレイピング前に robots.txt と利用規約を必ず確認**。私的・研究目的の範囲で、
  アクセス頻度を抑えて利用してください。`NetkeibaClient` は既定で 1.5 秒の
  ウェイトとキャッシュを入れています。
- 本ツールは学習・分析目的です。馬券購入は自己責任で。

## ロードマップ

- [x] **フェーズ1: ①②データ取得**（netkeiba／HTTPとパースの分離／オフラインテスト）
- [ ] フェーズ2: ③可視化（matplotlib で成績グラフ）
- [ ] フェーズ3: ④示唆出し（統計指標 + 生成AI による要約・コメント）
- [ ] フェーズ4: ⑤予想（scikit-learn で複勝/着順予測、生成AIで根拠説明）
- [ ] フェーズ5: バックテスト（的中率・回収率の検証）
