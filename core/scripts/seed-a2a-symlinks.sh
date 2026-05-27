#!/usr/bin/env bash
# seed-a2a-symlinks.sh — create hermes-a2a plugin symlinks for all Hermes profiles.
# Run: bash seed-a2a-symlinks.sh
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_SRC="$HERMES_HOME/plugins/hermes-a2a"
PROFILES_DIR="$HERMES_HOME/profiles"

if [ ! -d "$PLUGIN_SRC" ]; then
    echo "ERROR: hermes-a2a plugin not found at $PLUGIN_SRC"
    exit 1
fi

count=0
for profile_dir in "$PROFILES_DIR"/*/; do
    profile=$(basename "$profile_dir")
    plugins_dir="$profile_dir/plugins"
    target="$plugins_dir/hermes-a2a"

    mkdir -p "$plugins_dir"

    if [ -L "$target" ]; then
        echo "  [skip] $profile — symlink exists"
    elif [ -d "$target" ]; then
        echo "  [skip] $profile — real dir exists (not touching)"
    else
        ln -s "$PLUGIN_SRC" "$target"
        echo "  [created] $profile → hermes-a2a"
        count=$((count + 1))
    fi
done

echo ""
echo "Done. Created $count new symlinks."
echo "Verify: for d in $PROFILES_DIR/*/; do ls -la \$d/plugins/hermes-a2a 2>/dev/null; done"
