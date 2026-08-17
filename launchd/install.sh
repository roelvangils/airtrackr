#!/bin/bash
#
# Install (or reinstall) the AirTrackr LaunchAgents for the current user.
#
# Replaces the old imac/ setup, which hardcoded /Users/evelyn/Repos/airtrackr
# across twelve plists. Paths here are derived from wherever this repo lives.
#
#   ./launchd/install.sh            install and start both agents
#   ./launchd/install.sh --uninstall  stop and remove them
#
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LABELS=(com.airtrackr.api com.airtrackr.tracker)

uninstall() {
    for label in "${LABELS[@]}"; do
        if launchctl list "$label" >/dev/null 2>&1; then
            launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
            echo "  stopped $label"
        fi
        rm -f "$AGENTS_DIR/$label.plist"
    done
    echo "Uninstalled."
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    exit 0
fi

if [ ! -x "$REPO/venv/bin/python" ]; then
    echo "Error: $REPO/venv is missing. Run ./setup.sh first." >&2
    exit 1
fi
if [ ! -x "$REPO/swift/airtag_extractor" ]; then
    echo "Error: swift/airtag_extractor is missing. Run swift/build_universal.sh first." >&2
    exit 1
fi

mkdir -p "$AGENTS_DIR" "$REPO/logs"

for label in "${LABELS[@]}"; do
    template="$REPO/launchd/$label.plist.template"
    target="$AGENTS_DIR/$label.plist"

    sed "s|@@REPO@@|$REPO|g" "$template" > "$target"

    # bootout first so a reinstall picks up the new plist rather than silently
    # keeping the already-loaded one
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$target"
    echo "  installed $label"
done

echo
echo "Installed to $AGENTS_DIR (repo: $REPO)"
echo
echo "  status:  launchctl list | grep airtrackr"
echo "  logs:    tail -f $REPO/logs/tracker.log $REPO/logs/api.log"
echo "  remove:  ./launchd/install.sh --uninstall"
echo
echo "The tracker needs Accessibility permission, granted per-executable. macOS will"
echo "prompt on the first cycle; if it doesn't, add $REPO/venv/bin/python under"
echo "System Settings > Privacy & Security > Accessibility. Until then the extractor"
echo "exits 2 (ax_not_trusted) and records that in logs/tracker.log."
