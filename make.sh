#!/usr/bin/env bash
# Coach avatar build pipeline: config → Blender/MPFB generation → sparse morphs →
# per-texture budgets → out/. One command per coach, deterministic, CI-able on any Mac
# with the one-time setup from README.md.
#
#   ./make.sh male | female | all
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
GLTF="npx --yes @gltf-transform/cli"

build() {
  local sex="$1"
  local cfg="$DIR/$sex.json"
  local raw="$DIR/out/$sex-raw.glb"
  local sparse="$DIR/out/$sex-sparse.glb"
  local out="$DIR/out/$sex.glb"
  mkdir -p "$DIR/out"

  echo "== [$sex] texture pre-step (brand/tint) =="
  /usr/bin/python3 "$DIR/prepare_textures.py" "$cfg" || echo "   (no texture edits configured — skipped)"

  echo "== [$sex] blender generation =="
  "$BLENDER" --background --python "$DIR/generate_coach.py" -- "$cfg" "$raw" 2>&1 \
    | grep -E '^\[coach\]|Error|Traceback' || true
  [ -f "$raw" ] || { echo "FATAL: blender produced no GLB"; exit 1; }

  echo "== [$sex] texture budgets =="
  # Faces sell the avatar: skin stays at source resolution (2048-class).
  # Everything else gets budgeted: clothes/hair/eyes 1024, mouth interior + brows 512.
  $GLTF resize "$raw" "$out" --width 1024 --height 1024 \
    --pattern "{crude_male_tex,shorttex*,sneaker*,short0*,ponytail*,braid*,bob0*,female_sportsuit*,punkduck*,brown_eye,*_eye*,Tank_Top*,M_Swimming*}" 2>&1 | tail -1
  $GLTF resize "$out" "$out" --width 512 --height 512 \
    --pattern "{teeth*,tongue*,eyebrow*,eyelash*}" 2>&1 | tail -1
  # WebP re-encode: PNG diffuse maps are 3-4x larger for identical visual quality; webp is
  # universally supported by three.js/browsers this app targets.
  $GLTF webp "$out" "$out" 2>&1 | tail -1

  # NO geometry compression — hard-won lesson (gltf-transform 4.4.1):
  #   - sparse AFTER quantize ZEROES every morph delta → the avatar loads, looks perfect,
  #     and silently cannot lip-sync (shipped that way once; never again).
  #   - quantize alone DE-SPARSIFIES Blender's native sparse morph export (22MB → 39MB).
  # Blender already exports morphs sparsely; resize+webp above do the real shrinking.
  rm -f "$raw" "$sparse"

  echo "== [$sex] report =="
  ls -la "$out" | awk '{printf "   size: %.1f MB\n", $5/1e6}'
  python3 - "$out" <<'PY'
import json, struct, sys
data = open(sys.argv[1], 'rb').read()
jl, = struct.unpack('<I', data[12:16])
doc = json.loads(data[20:20+jl])
m0 = doc['meshes'][0]
prim = m0['primitives'][0]
names = m0.get('extras', {}).get('targetNames', [])
unskinned = [n.get('name') for n in doc['nodes'] if 'mesh' in n and 'skin' not in n]
macros = [n for n in names if n.startswith('$')]
print(f"   morphs: {len(names)} | visemes: {sum(1 for n in names if n.startswith('viseme_'))} | jawOpen: {'jawOpen' in names}")
print(f"   unskinned meshes: {unskinned or 'none'} | leftover macro keys: {macros or 'none'}")

# MORPH-LIVENESS GATE: a compressor once zeroed every delta while names/counts stayed
# intact — the coach loaded fine and silently couldn't lip-sync. Assert jawOpen's
# POSITION accessor has a real max displacement.
idx = names.index('jawOpen') if 'jawOpen' in names else -1
alive = False
if idx >= 0:
    acc = doc['accessors'][prim['targets'][idx]['POSITION']]
    mx = max(abs(v) for v in (acc.get('max') or [0]))
    alive = mx > 1e-4
    print(f"   jawOpen max displacement: {mx:.5f} {'OK' if alive else '*** DEAD MORPHS ***'}")
if not alive:
    sys.exit("FATAL: morph deltas are zero — the avatar cannot lip-sync. Aborting.")
PY
}

case "${1:-all}" in
  all) build male; build female ;;
  *)
    if [ -f "$DIR/$1.json" ]; then build "$1"
    else echo "usage: make.sh [all|<config-name>] — no $1.json here"; exit 1
    fi ;;
esac
echo "DONE. GLBs in $DIR/out/ — copy them wherever your app serves avatar models."
