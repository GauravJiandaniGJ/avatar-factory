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
    --pattern "{crude_male_tex,shorttex*,sneaker*,short0*,ponytail*,braid*,bob0*,female_sportsuit*,punkduck*,brown_eye,*_eye*}" 2>&1 | tail -1
  $GLTF resize "$out" "$out" --width 512 --height 512 \
    --pattern "{teeth*,tongue*,eyebrow*,eyelash*}" 2>&1 | tail -1
  # WebP re-encode: PNG diffuse maps are 3-4x larger for identical visual quality; webp is
  # universally supported by three.js/browsers this app targets.
  $GLTF webp "$out" "$out" 2>&1 | tail -1

  echo "== [$sex] geometry compression =="
  # ORDER IS LOAD-BEARING: quantize FIRST (16-bit vertex data, KHR_mesh_quantization —
  # native in three.js, NOT draco), sparse LAST. Quantize rewrites accessors densely, so
  # running it after sparse silently un-sparses 66 morphs and TRIPLES the file.
  $GLTF quantize "$out" "$out" 2>&1 | tail -1
  $GLTF sparse "$out" "$out" 2>&1 | tail -1
  rm -f "$raw" "$sparse"

  echo "== [$sex] report =="
  ls -la "$out" | awk '{printf "   size: %.1f MB\n", $5/1e6}'
  python3 - "$out" <<'PY'
import json, struct, sys
data = open(sys.argv[1], 'rb').read()
jl, = struct.unpack('<I', data[12:16])
doc = json.loads(data[20:20+jl])
m0 = doc['meshes'][0]
names = m0.get('extras', {}).get('targetNames', [])
unskinned = [n.get('name') for n in doc['nodes'] if 'mesh' in n and 'skin' not in n]
macros = [n for n in names if n.startswith('$')]
print(f"   morphs: {len(names)} | visemes: {sum(1 for n in names if n.startswith('viseme_'))} | jawOpen: {'jawOpen' in names}")
print(f"   unskinned meshes: {unskinned or 'none'} | leftover macro keys: {macros or 'none'}")
for im, bv in ((i, doc['bufferViews'][i['bufferView']]) for i in doc.get('images', [])):
    pass
PY
}

case "${1:-all}" in
  male)   build male ;;
  female) build female ;;
  all)    build male; build female ;;
  *) echo "usage: make.sh [male|female|all]"; exit 1 ;;
esac
echo "DONE. GLBs in $DIR/out/ — copy them wherever your app serves avatar models."
