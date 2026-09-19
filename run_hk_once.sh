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
  echo "[$(date -u '+%F %T')] HK analysis stage already complete; checking formal report delivery"
  timeout 90m .venv/bin/python -m reporting.daily_report \
    --market hk \
    --date "$MARKET_DAY" \
    --env-file .env.hk \
    --reuse-built
  exit $?
fi

source .venv/bin/activate
cp .env.hk .env
set -a
source .env
# one-shot cron 模式：main.py 完成数据分析阶段且不发送通知；正式报告由 reporting.daily_report 交付
export SCHEDULE_ENABLED=false
export SCHEDULE_RUN_IMMEDIATELY=false
export RUN_IMMEDIATELY=true
export FORCE_ONE_SHOT_MODE=true
export ANALYSIS_STAGE_ONLY=true
set +a
timeout 2h python3 main.py --no-notify
report_status=0
timeout 90m python3 -m reporting.daily_report \
  --market hk \
  --date "$MARKET_DAY" \
  --env-file .env.hk || report_status=$?
if [[ "$report_status" -eq 0 ]]; then
  touch "$MARKER_FILE"
else
  echo "[$(date -u '+%F %T')] HK report delivery failed; completion marker not written"
fi
exit "$report_status"
