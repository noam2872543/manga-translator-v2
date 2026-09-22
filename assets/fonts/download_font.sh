#!/usr/bin/env bash
# =====================================================================
# download_font.sh
# Download a set of Hebrew-capable TrueType fonts from Google Fonts into
# this folder so the renderer has nicely-shaped Hebrew glyphs even on a
# fresh machine.
#
# Usage:  bash download_font.sh
# =====================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# name -> url
declare -A FONTS=(
  ["VarelaRound-Regular.ttf"]="https://github.com/google/fonts/raw/main/ofl/varelaround/VarelaRound-Regular.ttf"
  ["DavidLibre-Regular.ttf"]="https://github.com/google/fonts/raw/main/ofl/davidlibre/DavidLibre-Regular.ttf"
  ["Assistant-Regular.ttf"]="https://github.com/google/fonts/raw/main/ofl/assistant/Assistant%5Bwght%5D.ttf"
)

echo "Downloading Hebrew-capable fonts into $DIR ..."
ok=0; fail=0
for name in "${!FONTS[@]}"; do
  url="${FONTS[$name]}"
  if [ -f "$name" ] && [ -s "$name" ]; then
    echo "  [skip] $name already present"
    ok=$((ok+1))
    continue
  fi
  if curl -fsSL -o "$name" "$url" 2>/dev/null && [ -s "$name" ]; then
    echo "  [ok]   $name ($(wc -c < "$name") bytes)"
    ok=$((ok+1))
  else
    echo "  [fail] $name ($url)"
    rm -f "$name"
    fail=$((fail+1))
  fi
done
echo "Done: $ok downloaded/skipped, $fail failed."
echo
echo "If all downloads failed you are offline.  The renderer will then fall"
echo "back to any system Hebrew font (e.g. DejaVuSans on most Linux distros)."
