#!/usr/bin/env bash
set -euo pipefail

# Cloud Run にデプロイ済みの keiba サービスを叩いて「予想」を実行するスクリプト。
#
# 前提:
#   1. ./deploy/keiba/deploy.sh で keiba サービスをデプロイ済み
#   2. 予想に使う過去ダービー + 対象レースを GCS に保存済み（手元の回線で fetch）
#        python3 -m keiba.cli fetch --derby-years 2019-2024 --past --out gs://<bucket>/keiba
#        python3 -m keiba.cli fetch --race-id <対象> --out gs://<bucket>/keiba
#   3. gcloud 認証済み（gcloud auth login）
#
# 使い方:
#   ./deploy/keiba/run_predict.sh 202605021211            # ML + 生成AI 両方
#   MODE=ml    ./deploy/keiba/run_predict.sh 202605021211 # ML のみ
#   MODE=genai ./deploy/keiba/run_predict.sh 202605021211 # 生成AI のみ
#
# 上書き可能な環境変数:
#   PROJECT_ID (default clean-pen-422206-d7), REGION (asia-northeast1),
#   SERVICE (keiba), MODE (both|ml|genai), TOKEN (KEIBA_FETCH_TOKEN を設定済みなら)

RACE_ID="${1:-}"
if [ -z "$RACE_ID" ]; then
    echo "usage: $0 <race_id>   (例: $0 202605021211)" >&2
    exit 1
fi

PROJECT_ID="${PROJECT_ID:-clean-pen-422206-d7}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-keiba}"
MODE="${MODE:-both}"
TOKEN="${TOKEN:-}"

# トークン保護を有効化している場合のクエリ文字列
Q=""
[ -n "$TOKEN" ] && Q="?token=${TOKEN}"
SEP="?"
[ -n "$TOKEN" ] && SEP="&"

echo "==> Service URL を取得中 (project=$PROJECT_ID region=$REGION service=$SERVICE)"
URL="$(gcloud run services describe "$SERVICE" --region "$REGION" \
        --project "$PROJECT_ID" --format 'value(status.url)')"
if [ -z "$URL" ]; then
    echo "✗ サービス URL が取得できません。先に deploy/keiba/deploy.sh でデプロイしてください。" >&2
    exit 1
fi
echo "    URL = $URL"

echo "==> ヘルスチェック"
curl -fsS "${URL}/healthz${Q}" && echo

# jq があれば整形、無ければ素のJSON
fmt() { if command -v jq >/dev/null 2>&1; then jq "$@"; else cat; fi; }

if [ "$MODE" = "ml" ] || [ "$MODE" = "both" ]; then
    echo ""
    echo "================= ⑤ML予想 (/predict) ================="
    curl -fsS "${URL}/predict/${RACE_ID}${Q}" \
      | fmt '{race_name, trained_on, top: (.ranking[:5] | map({pred_rank, horse_no, horse_name, show_prob}))}'
fi

if [ "$MODE" = "genai" ] || [ "$MODE" = "both" ]; then
    echo ""
    echo "============ ④⑤生成AI予想 (/genai-predict) ============"
    echo "(Claude API 呼び出しのため数十秒かかることがあります)"
    curl -fsS --max-time 600 "${URL}/genai-predict/${RACE_ID}${Q}" \
      | fmt '{race_name, trained_on,
              summary: .insights.summary,
              insights: (.insights.insights | map("[\(.weight)] \(.pattern)")),
              honmei: .prediction.honmei_horse_no,
              ranking: (.prediction.ranking | map({horse_no, horse_name, score, reason}))}'
fi

echo ""
echo "==> 完了。生のJSONが欲しい場合は直接 curl してください:"
echo "    curl \"${URL}/predict/${RACE_ID}\""
echo "    curl \"${URL}/genai-predict/${RACE_ID}\""
