#!/bin/bash
# Dialog Watcher for AirTrackr
# Runs every 3 seconds and auto-clicks Allow/OK on system dialogs

LOG_FILE="$HOME/Repos/airtrackr/logs/dialog_watcher.log"
CHECK_INTERVAL=3

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "Dialog watcher started (PID: $$)"

while true; do
    # Check for SecurityAgent (permission dialogs)
    osascript -e '
    tell application "System Events"
        if exists process "SecurityAgent" then
            tell process "SecurityAgent"
                repeat with w in windows
                    try
                        if exists button "Allow" of w then
                            click button "Allow" of w
                            return "clicked:SecurityAgent:Allow:" & (name of w)
                        end if
                        if exists button "OK" of w then
                            click button "OK" of w
                            return "clicked:SecurityAgent:OK:" & (name of w)
                        end if
                    end try
                end repeat
            end tell
        end if
    end tell
    return ""
    ' 2>/dev/null | while read -r result; do
        if [[ -n "$result" && "$result" == clicked:* ]]; then
            log "$result"
        fi
    done

    # Check for CoreServicesUIAgent (firewall dialogs)
    osascript -e '
    tell application "System Events"
        if exists process "CoreServicesUIAgent" then
            tell process "CoreServicesUIAgent"
                repeat with w in windows
                    try
                        if exists button "Allow" of w then
                            click button "Allow" of w
                            return "clicked:CoreServicesUIAgent:Allow:firewall"
                        end if
                    end try
                end repeat
            end tell
        end if
    end tell
    return ""
    ' 2>/dev/null | while read -r result; do
        if [[ -n "$result" && "$result" == clicked:* ]]; then
            log "$result"
        fi
    done

    # Check for UserNotificationCenter (TCC privacy dialogs like "would like to access")
    osascript -e '
    tell application "System Events"
        if exists process "UserNotificationCenter" then
            tell process "UserNotificationCenter"
                repeat with w in windows
                    try
                        if exists button "Allow" of w then
                            click button "Allow" of w
                            return "clicked:UserNotificationCenter:Allow:" & (name of w)
                        end if
                        if exists button "OK" of w then
                            click button "OK" of w
                            return "clicked:UserNotificationCenter:OK:" & (name of w)
                        end if
                    end try
                end repeat
            end tell
        end if
    end tell
    return ""
    ' 2>/dev/null | while read -r result; do
        if [[ -n "$result" && "$result" == clicked:* ]]; then
            log "$result"
        fi
    done

    # Check for TCC prompts in any process (generic fallback)
    osascript -e '
    tell application "System Events"
        repeat with proc in (processes whose background only is false)
            try
                tell proc
                    repeat with w in windows
                        try
                            -- Look for typical TCC dialog buttons
                            if exists button "Allow" of w then
                                set winName to name of w
                                if winName contains "would like to" or winName contains "wants to access" or winName is "" then
                                    click button "Allow" of w
                                    return "clicked:" & (name of proc) & ":Allow:" & winName
                                end if
                            end if
                            if exists button "Don'"'"'t Allow" of w then
                                -- This is a TCC dialog, click Allow if it exists
                                if exists button "Allow" of w then
                                    click button "Allow" of w
                                    return "clicked:" & (name of proc) & ":Allow:TCC"
                                end if
                            end if
                        end try
                    end repeat
                end tell
            end try
        end repeat
    end tell
    return ""
    ' 2>/dev/null | while read -r result; do
        if [[ -n "$result" && "$result" == clicked:* ]]; then
            log "$result"
        fi
    done

    sleep $CHECK_INTERVAL
done
