#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p logs state/run_locks state/run_markers

LOCK_FILE="state/run_locks/us_once.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -u '+%F %T')] US one-shot skipped: another run is in progress"
  exit 0
fi

MARKET_DAY="$(TZ=America/New_York date +%F)"
MARKER_FILE="state/run_markers/us_${MARKET_DAY}.done"
if [[ -f "$MARKER_FILE" ]]; then
  echo "[$(date -u '+%F %T')] US analysis stage already complete; checking formal report delivery"
  timeout 90m .venv/bin/python -m reporting.daily_report \
    --market us \
    --date "$MARKET_DAY" \
    --env-file .env.us \
    --reuse-built
  exit $?
fi

# 冬令时 (EST) 补等：crontab 统一 20:15 UTC，EST 下 16:00 ET 收盘 = 21:00 UTC
# 检测当前 UTC 时间 vs 收盘+15min(UTC)，若还没到则 sleep 到目标时间
MARKET_CLOSE_ET_HOUR=16
MARKET_CLOSE_ET_MIN=0
OFFSET_MIN=15
CLOSE_ET_EPOCH=$(TZ=America/New_York date -d "today ${MARKET_CLOSE_ET_HOUR}:${MARKET_CLOSE_ET_MIN}" +%s 2>/dev/null || true)
TARGET_EPOCH=$((CLOSE_ET_EPOCH + OFFSET_MIN * 60))
NOW_EPOCH=$(date -u +%s)
if [[ -n "$CLOSE_ET_EPOCH" ]] && [[ "$NOW_EPOCH" -lt "$TARGET_EPOCH" ]]; then
  WAIT_SEC=$((TARGET_EPOCH - NOW_EPOCH))
  echo "[$(date -u '+%F %T')] US 市场尚未收盘 (EST 冬令时)，等待 ${WAIT_SEC}s 至 $(date -u -d @${TARGET_EPOCH} '+%F %T') UTC"
  sleep "$WAIT_SEC"
fi

source .venv/bin/activate
cp .env.us .env
set -a
source .env
# one-shot cron 模式：main.py 完成数据分析阶段且不发送通知；正式报告由 reporting.daily_report 交付
export SCHEDULE_ENABLED=false
export SCHEDULE_RUN_IMMEDIATELY=false
export RUN_IMMEDIATELY=true
export FORCE_ONE_SHOT_MODE=true
export ANALYSIS_STAGE_ONLY=true
set +a
timeout 45m python3 main.py --no-notify
report_status=0
timeout 90m python3 -m reporting.daily_report \
  --market us \
  --date "$MARKET_DAY" \
  --env-file .env.us || report_status=$?
touch "$MARKER_FILE"
exit "$report_status"
