# Avatar Factory

Generate **rigged, lip-sync-ready 3D human avatars from a JSON config** — fully headless,
$0 per avatar, no external service. Physique is code: `"muscle": 1.0` IS a bodybuilder.
Faces, bodies, outfits, skin tone, even a printed t-shirt brand — all config.

Output GLBs are [TalkingHead](https://github.com/met4citizen/TalkingHead)-compatible:
Mixamo rig, 52 ARKit face shapes + Oculus visemes (real-time lip-sync), ~5MB each,
loadable by any three.js / glTF pipeline.

Born after Ready Player Me shut down (Jan 2026) took thousands of apps' avatar pipelines
with it — owning your avatar pipeline beats renting one.

```
./setup.sh          # one-time: Blender + MPFB + asset packs (~600MB, macOS)
./make.sh all       # build male.json + female.json → out/*.glb
```

## How it works

`make.sh` runs the pipeline per config:

1. **`prepare_textures.py`** — edits COPIES of source textures per config: multiply skin
   tint (e.g. warm Indian tone), text prints on clothing (brand a t-shirt from JSON).
2. **`generate_coach.py`** (Blender, headless) — builds the human with MPFB (MakeHuman):
   phenotype macros + per-muscle-group detail targets, assets (skin/hair/outfit/eyes),
   TalkingHead Mixamo rig + weights, ARKit/viseme shape keys, one baked subdivision level
   (shape keys preserved), node-level material fixing (OPAQUE/alpha-clip), outward normals.
3. **Compression** — per-texture resize budgets (skin 2048, clothes 1024, mouth 512) →
   WebP → 16-bit quantize (KHR_mesh_quantization) → sparse morph storage.
   **Order is load-bearing**: quantize before sparse — the reverse un-sparses every morph
   and triples the file.

## Configs

`male.json` / `female.json` are complete examples (an Indian bodybuilder coach and an
Indian athlete). Everything is data:

- `phenotype` — gender/age/muscle/weight/height/proportions/race mix (0–1 sliders)
- `targets` — MPFB detail targets: `torso-vshape-incr`, `l-upperarm-muscle-incr`,
  `chin-bones-incr`, … (physique + face shaping)
- `skin` / `hair` / `eyes` / `clothes` — asset fragments from the MakeHuman packs
- `texture_edits` — skin `tint` {rgb, strength}, clothing `print` {text, box, fill}
- `subdiv` — set `false` to skip the head-detail subdivision

Add a new person = add a new JSON file, run `./make.sh <name>`.

## Hard-won export lessons (fixed in the generator — don't regress)

- **Flatten the phenotype into the basis** before building face morphs: engines zero all
  morph influences at load, so live macro keys = the body collapses at runtime while
  Blender re-imports look fine.
- **Transfer bone weights body→children**: MakeHuman assets bind to the basemesh and
  export as STATIC meshes otherwise (frozen clothes/hair on a moving body).
- **Fix alpha at the node level** (sever links / insert GREATER_THAN): Blender 4.5's
  `material.blend_method` is a deprecated no-op and everything exports as BLEND
  (sorting artifacts, milky faces).
- **Recalculate normals outward** — with backface culling, inward patches disappear.

## Licensing

- Tool: MIT (see LICENSE). Vendored TalkingHead rig/morph files: MIT (Mika Suominen).
- **Generated avatars are yours** — commercially usable. Ingredients: MakeHuman CC0 packs
  (no conditions) + optional CC-BY packs (`pants03`, `shoes02`: credit punkduck,
  Elvaerwyn, Mindfront, culturalibre — one line in your app's credits satisfies it).
- Blender + MPFB are GPL tools; GPL does not extend to generated output.

## Requirements

macOS (paths assume it; Linux works with `BLENDER` + `MPFB_DATA` env overrides),
Blender 4.5+, Node (for `npx @gltf-transform/cli`), system Python 3 with Pillow.
