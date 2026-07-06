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


def make_normal_map(diffuse, strength=1.6):
    """Tangent-space normal map derived from the diffuse: mid-frequency relief only.

    The MakeHuman skins ship diffuse-only, and flat normals are most of the 'plastic
    mannequin' look. Real scans aren't available, but painted diffuse shading correlates
    well with surface relief: grayscale → subtract a heavy blur (keeps feature-scale
    detail, drops broad lighting) → Sobel gradients → RGB normal. Pure Pillow kernels
    (no numpy on the system python); the output is slightly unnormalized, which is fine —
    three.js renormalizes sampled normals in the fragment shader. `strength` scales
    apparent depth; keep subtle — overdriven fake normals read as dirt, not pores."""
    from PIL import ImageChops, ImageFilter

    gray = diffuse.convert("L")
    # Mid-frequency band: detail minus a strong blur (radius ~1% of texture size),
    # recentered at 128 so both bumps and dents survive the unsigned subtract.
    radius = max(4, diffuse.width // 100)
    blur = gray.filter(ImageFilter.GaussianBlur(radius))
    band = ImageChops.subtract(gray, blur, scale=1, offset=128)
    band = band.filter(ImageFilter.GaussianBlur(1))          # denoise single pixels

    sobel_x = ImageFilter.Kernel((3, 3), [-1, 0, 1, -2, 0, 2, -1, 0, 1], scale=4, offset=128)
    sobel_y = ImageFilter.Kernel((3, 3), [-1, -2, -1, 0, 0, 0, 1, 2, 1], scale=4, offset=128)
    dx = band.filter(sobel_x)
    dy = band.filter(sobel_y)

    # R = 0.5 - strength*dx, G = 0.5 + strength*dy (OpenGL-style +Y, what glTF expects
    # from Blender's exporter), B ≈ 1. Point ops keep it fast on 2048² images.
    k = strength
    r = dx.point(lambda v: max(0, min(255, int(128 - (v - 128) * k))))
    g = dy.point(lambda v: max(0, min(255, int(128 + (v - 128) * k))))
    b = Image.new("L", gray.size, 255)
    return Image.merge("RGB", (r, g, b))


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
        raw = Image.open(src)
        alpha = raw.getchannel("A") if raw.mode == "RGBA" else None
        img = raw.convert("RGB")
        if "tint" in spec:
            img = apply_tint(img, spec["tint"]["rgb"], spec["tint"].get("strength", 0.3))
        if "print" in spec:
            img = apply_print(img, spec["print"])
        dest = out_dir / f"{image_name}.png"
        if alpha is not None:
            img = img.convert("RGBA")
            img.putalpha(alpha)          # restore the original cutout (hair cards etc.)
        img.save(dest)
        manifest[image_name] = str(dest)

        # Optional derived normal map (skin realism): "normal": true or {"strength": 1.6}.
        # Registered in the manifest as <image>__normal; generate_coach.py wires it into
        # the material whose base color uses <image>.
        if spec.get("normal"):
            nspec = spec["normal"] if isinstance(spec["normal"], dict) else {}
            nmap = make_normal_map(img, nspec.get("strength", 1.6))
            ndest = out_dir / f"{image_name}__normal.png"
            nmap.save(ndest)
            manifest[f"{image_name}__normal"] = str(ndest)

        print(f"[tex] {image_name}: {src.name} → {dest.name} "
              f"({'tint ' if 'tint' in spec else ''}{'print ' if 'print' in spec else ''}"
              f"{'normal' if spec.get('normal') else ''})")

    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[tex] manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_textures.py <config.json>")
    raise SystemExit(main(sys.argv[1]))
