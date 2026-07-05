#!/usr/bin/env /usr/bin/python3
"""Texture pre-step for the coach generator: per-config edits (brand print, skin tint)
applied to COPIES of the source MakeHuman textures before Blender runs. generate_coach.py
then points the corresponding Blender images at the edited copies via `texture_overrides`
written into the work dir. Requires Pillow (present on the system python3 at /usr/bin).

Config (`texture_edits` in male.json / female.json):
  "texture_edits": {
    "<image name as it appears in Blender/GLB>": {
      "source": "<absolute or data-root-relative path to the source texture>",
      "tint":  {"rgb": [r,g,b], "strength": 0.0-1.0},      # optional multiply-tint
      "print": {"text": "COACH", "box": [l,t,r,b], "fill": [r,g,b], "font_px": 54}  # optional
    }, ...
  }
Outputs edited copies to tools/coach-avatar/out/tex/<image name>.png and a manifest at
out/tex/<config name>.overrides.json consumed by generate_coach.py.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

DIR = Path(__file__).parent
# MPFB user-data root (asset packs live here). Override with MPFB_DATA for other
# Blender versions/platforms.
import os
import glob as _glob
def _default_data_root():
    env = os.environ.get("MPFB_DATA")
    if env:
        return Path(env)
    hits = sorted(_glob.glob(str(Path.home() / "Library/Application Support/Blender/*/extensions/.user/blender_org/mpfb/data")))
    return Path(hits[-1]) if hits else Path.home() / ".mpfb/data"
DATA_ROOT = _default_data_root()

FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def load_font(px):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, px)
        except Exception:
            continue
    return ImageFont.load_default()


def apply_tint(img, rgb, strength):
    """MULTIPLY-tint toward rgb: darkens + warms while preserving all shading detail (a
    plain alpha blend washes out and barely registers — the first version's mistake).
    Neutral gray in the tint = no-op, so rgb sets both hue and depth. strength 0..1 fades
    between original and fully multiplied."""
    from PIL import ImageChops
    # Normalize the multiply layer around the tint color relative to a mid skin tone, so
    # tint [156,108,74] on a light-skin diffuse lands near that tone instead of near-black.
    base = 208.0
    layer = Image.new("RGB", img.size, tuple(min(255, int(c / base * 255)) for c in rgb))
    multiplied = ImageChops.multiply(img, layer)
    tinted = Image.blend(img, multiplied, strength)
    return ImageEnhance.Color(tinted).enhance(1.0 + 0.10 * strength)


def apply_print(img, spec):
    draw = ImageDraw.Draw(img)
    font = load_font(spec.get("font_px", 54))
    text = spec["text"]
    l, t, r, b = spec["box"]
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, cy = (l + r) // 2, (t + b) // 2
    draw.text((cx - w / 2, cy - h / 2 - bbox[1]), text, fill=tuple(spec.get("fill", [16, 24, 60])), font=font)
    return img


def main(cfg_path):
    cfg = json.loads(Path(cfg_path).read_text())
    edits = cfg.get("texture_edits") or {}
    out_dir = DIR / "out" / "tex"
    manifest_path = out_dir / f"{cfg['name']}.overrides.json"
    if not edits:
        # No edits: remove any stale manifest so the generator doesn't pick up old files.
        if manifest_path.exists():
            manifest_path.unlink()
        print(f"[tex] {cfg['name']}: no texture_edits — nothing to do")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for image_name, spec in edits.items():
        src = Path(spec["source"])
        if not src.is_absolute():
            src = DATA_ROOT / spec["source"]
        img = Image.open(src).convert("RGB")
        if "tint" in spec:
            img = apply_tint(img, spec["tint"]["rgb"], spec["tint"].get("strength", 0.3))
        if "print" in spec:
            img = apply_print(img, spec["print"])
        dest = out_dir / f"{image_name}.png"
        img.save(dest)
        manifest[image_name] = str(dest)
        print(f"[tex] {image_name}: {src.name} → {dest.name} "
              f"({'tint ' if 'tint' in spec else ''}{'print' if 'print' in spec else ''})")

    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[tex] manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_textures.py <config.json>")
    raise SystemExit(main(sys.argv[1]))
