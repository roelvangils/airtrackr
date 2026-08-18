#!/bin/bash
set -euo pipefail

# Build the AirTrackr Swift tools.
#
# Universal (Intel + Apple Silicon) when the toolchain can still do it. As of the
# macOS 27 Command Line Tools it often cannot: the Swift back-deployment libraries
# (libswiftCompatibility56.a and friends) ship arm64/arm64e slices only, so linking
# -target x86_64-apple-macos11.0 fails with
#   ld: warning: ... fat file missing arch 'x86_64', file has 'arm64,arm64e'
#   "__swift_FORCE_LOAD_$_swiftCompatibility56", referenced from: ...
# That is a missing toolchain slice, not a problem with the source, so this script
# degrades to an arm64-only build and says so rather than failing.
#
# Nothing is deleted until a replacement exists. An earlier version cleaned up first
# and built second, so the day the x86_64 link broke it removed a working extractor
# and left the tracker with no binary at all.

echo "Building AirTrackr Swift tools..."
echo "================================================"

cd "$(dirname "$0")"

TMPDIR_BUILD="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_BUILD"' EXIT

# build_tool <source.swift> <output-name>
build_tool() {
    local source="$1" output="$2"
    local slices=()

    if [ ! -f "$source" ]; then
        echo "❌ Error: $source not found!"
        return 1
    fi

    echo ""
    echo "🔨 $output"

    # Apple Silicon: required. If this fails, the build fails.
    if swiftc -O "$source" -o "$TMPDIR_BUILD/${output}_arm64" \
            -target arm64-apple-macos11.0 2>"$TMPDIR_BUILD/${output}.arm64.log"; then
        slices+=("$TMPDIR_BUILD/${output}_arm64")
        echo "   ✅ arm64"
    else
        echo "   ❌ arm64 build failed:"
        sed 's/^/      /' "$TMPDIR_BUILD/${output}.arm64.log" | grep -E "error" | head -5
        return 1
    fi

    # Intel: nice to have. Skipped with a note when the toolchain lacks the slices.
    if swiftc -O "$source" -o "$TMPDIR_BUILD/${output}_x86_64" \
            -target x86_64-apple-macos11.0 2>"$TMPDIR_BUILD/${output}.x86.log"; then
        slices+=("$TMPDIR_BUILD/${output}_x86_64")
        echo "   ✅ x86_64"
    else
        echo "   ⚠️  x86_64 unavailable in this toolchain — building arm64-only"
        echo "      (this Mac is Apple Silicon, so the tracker is unaffected)"
    fi

    if [ "${#slices[@]}" -gt 1 ]; then
        lipo -create -output "$TMPDIR_BUILD/$output" "${slices[@]}"
    else
        cp "${slices[0]}" "$TMPDIR_BUILD/$output"
    fi

    # Only now replace the binary that is in use.
    mv "$TMPDIR_BUILD/$output" "./$output"
    chmod +x "./$output"
    echo "   📦 $(lipo -info "./$output" | sed 's/.*: //') · $(ls -lh "./$output" | awk '{print $5}')"
}

# The extractor: reads Find My through the Accessibility APIs.
build_tool airtag_extractor.swift airtag_extractor

# Companion tool: Find My cannot build a window without a display, and on a lid-shut
# Mac that display is DeskPad's virtual one. This sets its resolution and doubles as
# the tracker's "is there a display at all?" probe.
build_tool set_display_mode.swift set_display_mode

echo ""
echo "✨ Done."
