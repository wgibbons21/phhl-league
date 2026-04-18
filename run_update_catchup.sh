#!/usr/bin/env bash
# run_update_catchup.sh — Runs on Mac startup/login.
# If a scheduled Sunday or Monday update was missed (Mac was off), runs now.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAST_RUN_FILE="$SCRIPT_DIR/data/.last_run_epoch"
LOG_FILE="$SCRIPT_DIR/data/update.log"

NOW=$(date +%s)

# Read last run time (default to 0 if never run)
if [ -f "$LAST_RUN_FILE" ]; then
    LAST_RUN=$(cat "$LAST_RUN_FILE")
else
    LAST_RUN=0
fi

SECONDS_SINCE_LAST=$((NOW - LAST_RUN))
HOURS_SINCE_LAST=$((SECONDS_SINCE_LAST / 3600))

# If last run was more than 6 days ago, we definitely missed at least one weekly window
# Also run if it's been > 30h (catches a missed Sunday that tried and found nothing,
# meaning we missed the Monday retry too)
SHOULD_RUN=0

if [ "$HOURS_SINCE_LAST" -gt 144 ]; then
    # More than 6 days — definitely missed a full weekly cycle
    SHOULD_RUN=1
    REASON="missed weekly update (last run ${HOURS_SINCE_LAST}h ago)"
elif [ "$HOURS_SINCE_LAST" -gt 30 ]; then
    # More than 30h — check if we're now past a window we would have missed
    # Get day of week: 0=Sun, 1=Mon, 2=Tue...
    DOW=$(date +%w)
    # If it's Monday or later and we haven't run since before Sunday 8pm, catch up
    if [ "$DOW" -ge 1 ] && [ "$DOW" -le 3 ]; then
        SHOULD_RUN=1
        REASON="missed Sunday/Monday window (last run ${HOURS_SINCE_LAST}h ago, today is day $DOW)"
    fi
fi

if [ "$SHOULD_RUN" -eq 1 ]; then
    echo "=====================================" >> "$LOG_FILE"
    echo "CATCHUP RUN triggered at $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG_FILE"
    echo "Reason: $REASON" >> "$LOG_FILE"
    osascript -e 'display notification "Missed a scheduled update — running catch-up now..." with title "🥒 Disco Pickles" subtitle "Catch-up Update" sound name "Purr"'
    exec "$SCRIPT_DIR/run_update.sh"
else
    # Nothing to do — silently exit
    exit 0
fi
