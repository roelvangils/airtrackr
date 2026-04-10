-- Dialog Watcher for AirTrackr
-- Automatically dismisses permission dialogs by clicking Allow/OK
-- Must be saved as "Application" with "Stay open after run handler" checked
-- Or run via: osascript -e 'run application "dialog_watcher"'

property checkInterval : 3 -- seconds between checks
property logFile : "/Users/evelyn/Repos/airtrackr/logs/dialog_watcher.log"

on logMessage(msg)
    do shell script "echo \"[$(date '+%Y-%m-%d %H:%M:%S')] " & msg & "\" >> " & logFile
end logMessage

on run
    logMessage("Dialog watcher started (PID: " & (do shell script "echo $$") & ")")
end run

on idle
    try
        tell application "System Events"
            -- SecurityAgent handles permission dialogs (Accessibility, etc.)
            if exists process "SecurityAgent" then
                tell process "SecurityAgent"
                    repeat with w in windows
                        try
                            if exists button "Allow" of w then
                                click button "Allow" of w
                                my logMessage("Clicked 'Allow' in SecurityAgent window: " & (name of w))
                            end if
                            if exists button "OK" of w then
                                click button "OK" of w
                                my logMessage("Clicked 'OK' in SecurityAgent window: " & (name of w))
                            end if
                            if exists button "Open System Settings" of w then
                                -- Don't click this one, just log it
                                my logMessage("WARNING: 'Open System Settings' dialog detected - needs manual intervention")
                            end if
                        end try
                    end repeat
                end tell
            end if

            -- CoreServicesUIAgent handles firewall dialogs
            if exists process "CoreServicesUIAgent" then
                tell process "CoreServicesUIAgent"
                    repeat with w in windows
                        try
                            if exists button "Allow" of w then
                                click button "Allow" of w
                                my logMessage("Clicked 'Allow' in CoreServicesUIAgent (firewall)")
                            end if
                        end try
                    end repeat
                end tell
            end if

            -- UserNotificationCenter handles some notifications
            if exists process "UserNotificationCenter" then
                tell process "UserNotificationCenter"
                    repeat with w in windows
                        try
                            if exists button "Allow" of w then
                                click button "Allow" of w
                                my logMessage("Clicked 'Allow' in UserNotificationCenter")
                            end if
                            if exists button "OK" of w then
                                click button "OK" of w
                                my logMessage("Clicked 'OK' in UserNotificationCenter")
                            end if
                        end try
                    end repeat
                end tell
            end if
        end tell
    on error errMsg
        my logMessage("ERROR: " & errMsg)
    end try

    return checkInterval
end idle

on quit
    logMessage("Dialog watcher stopped")
    continue quit
end quit
