#!/usr/bin/env bash
# run_update.sh — Weekly league update runner with retry logic
#
# Scheduled via cron:
#   Sunday  8pm ET → first attempt
#   Monday  8pm ET → retry if Sunday found no new scores
#
# Sends macOS notifications on success, no-change, or error.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/data/update.log"
RETRY_FLAG="$SCRIPT_DIR/data/.retry_pending"
LAST_RUN_FILE="$SCRIPT_DIR/data/.last_run_epoch"
PYTHON=/usr/bin/python3

# Rotate log if > 1MB
[ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 1048576 ] && \
    tail -n 500 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"

echo "=====================================" >> "$LOG_FILE"
echo "Run: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG_FILE"

# Run the update and capture output
OUTPUT=$("$PYTHON" "$SCRIPT_DIR/update_league.py" 2>&1)
EXIT_CODE=$?
echo "$OUTPUT" >> "$LOG_FILE"

if [ $EXIT_CODE -ne 0 ]; then
    echo "STATUS: ERROR (exit $EXIT_CODE)" >> "$LOG_FILE"
    osascript -e 'display notification "League update failed — check update.log for details." with title "🥒 Disco Pickles" subtitle "Update Error" sound name "Basso"'
    exit 1
fi

# Count how many real score changes were detected (ignore placeholder noise)
CHANGE_COUNT=$(echo "$OUTPUT" | grep -c "SCORE ENTERED:")

if [ "$CHANGE_COUNT" -gt 0 ]; then
    echo "STATUS: $CHANGE_COUNT new score(s) found." >> "$LOG_FILE"
    # Clear any pending retry flag
    rm -f "$RETRY_FLAG"

    # Pull out DP record for notification
    DP_LINE=$(echo "$OUTPUT" | grep "Record:")
    osascript -e "display notification \"$CHANGE_COUNT new score(s) posted. $DP_LINE\" with title \"🥒 Disco Pickles\" subtitle \"League Updated ✅\" sound name \"Glass\""
else
    echo "STATUS: No new scores found." >> "$LOG_FILE"

    # Check if this is Sunday (day 0) — set retry flag; if Monday (day 1) — clear it
    DOW=$(date +%w)  # 0=Sun, 1=Mon
    if [ "$DOW" -eq 0 ]; then
        touch "$RETRY_FLAG"
        echo "STATUS: Retry scheduled for Monday 8pm." >> "$LOG_FILE"
        osascript -e 'display notification "No new scores yet. Will retry Monday evening." with title "🥒 Disco Pickles" subtitle "Scores Not Posted Yet" sound name "Purr"'
    else
        # Monday retry — still nothing, notify user to check manually
        rm -f "$RETRY_FLAG"
        echo "STATUS: Still no scores after retry." >> "$LOG_FILE"
        osascript -e 'display notification "Still no scores posted. Check DaySmart manually." with title "🥒 Disco Pickles" subtitle "Scores Still Pending ⚠️" sound name "Sosumi"'
    fi
fi

# Record the time of this run
date +%s > "$LAST_RUN_FILE"

# Sync to GitHub Pages repo
REPO=~/nobackup/phhl-league
if [ -d "$REPO/.git" ]; then
    cp /Users/wgibbons/Desktop/10U_ADV_League_6130.html "$REPO/index.html"
    cp "$SCRIPT_DIR/data/league_6130.json" "$REPO/data/league_6130.json"
    cd "$REPO"
    export PATH="/opt/homebrew/bin:$PATH"
    git add index.html data/league_6130.json
    if ! git diff --cached --quiet; then
        git commit -m "Weekly update: $(date '+%Y-%m-%d %H:%M')" >> "$LOG_FILE" 2>&1
        git push origin main >> "$LOG_FILE" 2>&1
        echo "STATUS: Pushed updated index.html to GitHub Pages." >> "$LOG_FILE"
    fi
fi

echo "" >> "$LOG_FILE"
exit 0
