#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p logs state/run_locks state/run_markers

LOCK_FILE="state/run_locks/hk_once.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -u '+%F %T')] HK one-shot skipped: another run is in progress"
  exit 0
fi

MARKET_DAY="$(TZ=Asia/Hong_Kong date +%F)"
MARKER_FILE="state/run_markers/hk_${MARKET_DAY}.done"
if [[ -f "$MARKER_FILE" ]]; then
  echo "[$(date -u '+%F %T')] HK background baseline already complete; checking V2 delivery"
  timeout 90m .venv/bin/python -m v2.shadow_delivery \
    --market hk \
    --date "$MARKET_DAY" \
    --env-file .env.hk \
    --delivery-mode v2 \
    --reuse-built
  exit $?
fi

source .venv/bin/activate
cp .env.hk .env
set -a
source .env
# one-shot cron 模式：旧版仅生成后台配对基线，关闭全部通知；读者交付由 V2 完成
export SCHEDULE_ENABLED=false
export SCHEDULE_RUN_IMMEDIATELY=false
export RUN_IMMEDIATELY=true
export FORCE_ONE_SHOT_MODE=true
export BACKGROUND_COMPARISON_ONLY=true
set +a
timeout 2h python3 main.py --no-notify
shadow_status=0
timeout 90m python3 -m v2.shadow_delivery \
  --market hk \
  --date "$MARKET_DAY" \
  --env-file .env.hk \
  --delivery-mode v2 || shadow_status=$?
touch "$MARKER_FILE"
exit "$shadow_status"
