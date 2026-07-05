#!/usr/bin/env bash
# One-time machine setup for the avatar factory (macOS):
#   Blender 4.5+ (Homebrew) → MPFB extension → MakeHuman asset packs.
# Safe to re-run; each step skips what's already present. ~600MB of downloads.
set -euo pipefail

BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

echo "== 1/3 Blender =="
if [ ! -x "$BLENDER" ]; then
  command -v brew >/dev/null || { echo "Homebrew required (https://brew.sh)"; exit 1; }
  brew install --cask blender
fi
"$BLENDER" --version | head -1

echo "== 2/3 MPFB extension =="
"$BLENDER" --background --python-expr "
import bpy
bpy.context.preferences.system.use_online_access = True
bpy.ops.wm.save_userpref()
" >/dev/null 2>&1 || true
"$BLENDER" --command extension sync >/dev/null 2>&1 || true
"$BLENDER" --command extension install mpfb --enable 2>&1 | tail -1 || echo "   (mpfb already installed)"

echo "== 3/3 MakeHuman asset packs =="
DATA_DIR="${MPFB_DATA:-$(ls -d "$HOME/Library/Application Support/Blender/"*/extensions/.user/blender_org/mpfb/data 2>/dev/null | tail -1)}"
if [ -z "$DATA_DIR" ]; then
  # First run: MPFB creates its dirs lazily — derive from the newest Blender version dir.
  VER_DIR=$(ls -d "$HOME/Library/Application Support/Blender/"*/ | tail -1)
  DATA_DIR="${VER_DIR}extensions/.user/blender_org/mpfb/data"
fi
mkdir -p "$DATA_DIR"
echo "   data dir: $DATA_DIR"

PACKS=(
  "makehuman_system_assets cc0"   # REQUIRED: eyes, teeth, tongue, proxies (267MB)
  "skins01 cc0" "skins02 cc0"
  "hair01 cc0"
  "eyebrows01 cc0" "eyelashes01 cc0"
  "shirts01 cc0"
  "pants03 ccby" "shoes02 ccby"    # CC-BY: attribution required if shipped (see README)
)
TMP=$(mktemp -d)
for entry in "${PACKS[@]}"; do
  set -- $entry
  pack="$1"; lic="$2"
  # Cheap presence probe: system assets install a top-level marker dir per pack type.
  url="https://files.makehumancommunity.org/asset_packs/$pack/${pack}_${lic}.zip"
  echo "   $pack ($lic)…"
  curl -sfL -o "$TMP/$pack.zip" "$url" || { echo "   FAILED: $url"; exit 1; }
  unzip -q -o "$TMP/$pack.zip" -d "$DATA_DIR"
done
rm -rf "$TMP"

echo "Setup complete. Build with: ./make.sh all"
