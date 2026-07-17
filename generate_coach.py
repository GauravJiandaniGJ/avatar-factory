# Coach avatar generator — builds a TalkingHead-compatible coach GLB from a JSON config,
# fully headless. This is "our own avatar tool": physique comes from MakeHuman macro sliders
# (muscle/weight/height/...), assets (skin/hair/outfit) from the CC0/CC-BY MakeHuman community
# packs, the rig + ARKit/viseme morphs from the TalkingHead project's MPFB workflow files.
#
#   blender --background --python tools/coach-avatar/generate_coach.py -- \
#       tools/coach-avatar/male.json /tmp/male.glb [/tmp/preview.png]
#
# Prereqs (one-time, see tools/coach-avatar/README.md):
#   - Blender 4.5+ with the MPFB extension installed and enabled
#   - MakeHuman asset packs extracted into MPFB's user data dir
#   - TalkingHead workflow files (talkinghead.mpfbskel, talkinghead.mhw,
#     talkinghead-targets.zip, talkinghead-addon.py) in tools/coach-avatar/vendor/
#
# Everything scripted here mirrors the manual Blender workflow documented in
# https://github.com/met4citizen/TalkingHead/blob/main/blender/MPFB/MPFB.md
import bpy
import json
import math
import sys
from pathlib import Path

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(ARGS) < 2:
    raise SystemExit("usage: blender --background --python generate_coach.py -- <config.json> <out.glb> [preview.png]")
CONFIG_PATH, OUT_GLB = Path(ARGS[0]), Path(ARGS[1])
PREVIEW = Path(ARGS[2]) if len(ARGS) > 2 else None
VENDOR = Path(__file__).parent / "vendor"

CFG = json.loads(CONFIG_PATH.read_text())

# ---- MPFB services ------------------------------------------------------------------------
from bl_ext.blender_org.mpfb.services.humanservice import HumanService
from bl_ext.blender_org.mpfb.services.objectservice import ObjectService
from bl_ext.blender_org.mpfb.services.rigservice import RigService
from bl_ext.blender_org.mpfb.services.clothesservice import ClothesService
from bl_ext.blender_org.mpfb.entities.rig import Rig
from bl_ext.blender_org.mpfb.entities.clothes.mhclo import Mhclo

# ---- TalkingHead addon helpers (plain functions; exec the file, skip registration) ---------
th = {"__file__": str(VENDOR / "talkinghead-addon.py"), "__name__": "talkinghead_helpers"}
exec(compile((VENDOR / "talkinghead-addon.py").read_text(), "talkinghead-addon.py", "exec"), th)

def log(*a):
    print("[coach]", *a, flush=True)

# Start from an EMPTY scene — the default Cube/Light/Camera would ride into the GLB
# (the select-all export grabs everything).
bpy.ops.wm.read_factory_settings(use_empty=True)

# ---- 1) Create the human (physique + assets) ----------------------------------------------
human_info = HumanService._create_default_human_info_dict()
human_info["name"] = CFG["name"]
human_info["phenotype"] = CFG["phenotype"]
human_info["eyes"] = CFG.get("eyes", "low-poly/low-poly.mhclo")
human_info["teeth"] = CFG.get("teeth", "teeth_base/teeth_base.mhclo")
human_info["tongue"] = CFG.get("tongue", "tongue01/tongue01.mhclo")
human_info["eyebrows"] = CFG.get("eyebrows", "")
human_info["eyelashes"] = CFG.get("eyelashes", "")
human_info["hair"] = CFG.get("hair", "")
human_info["clothes"] = CFG.get("clothes", [])
human_info["targets"] = CFG.get("targets", [])   # detail targets: the bodybuilder layer on top of the macros
human_info["skin_mhmat"] = CFG.get("skin", "")
# GAMEENGINE = plain Principled-BSDF PBR materials — the ONLY node layout the glTF exporter
# maps faithfully (MAKESKIN/ENHANCED_SSS node graphs export as black in three.js).
# EXCEPTION: eyes keep MAKESKIN — the GAMEENGINE eye material loses the iris texture and
# exports as blank white spheres; the MakeSkin eye graph survives export.
human_info["skin_material_type"] = "GAMEENGINE"
human_info["clothes_material_type"] = "GAMEENGINE"
human_info["eyes_material_type"] = "MAKESKIN"

settings = HumanService.get_default_deserialization_settings()
settings["subdiv_levels"] = 0          # mobile budget — no subdivision
settings["load_clothes"] = True
settings["scale"] = 0.1                # MPFB decimeter default; final scale set on the armature

log("creating human", CFG["name"])
basemesh = HumanService.deserialize_from_dict(human_info, settings)
log("human created:", basemesh.name, "verts:", len(basemesh.data.vertices))

# FLATTEN the phenotype into the mesh. MPFB applies macros (gender/age/muscle/race) and our
# detail targets as ACTIVE shape keys — exporting those alongside the face morphs is what
# mangled the avatar in-app: TalkingHead zeroes every morph influence at load, stripping the
# phenotype and collapsing the body/face to the raw base shape under the fitted hair/clothes
# (Blender re-import kept the key values, which is why renders looked fine). Bake the current
# mix into the basis so ONLY the ARKit/viseme keys built later are exported, all zero-valued.
def flatten_shape_keys(obj):
    if not obj.data.shape_keys:
        return
    obj.shape_key_add(name="__flat__", from_mix=True)
    for kb in [k for k in obj.data.shape_keys.key_blocks if k.name != "__flat__"]:
        obj.shape_key_remove(kb)
    obj.shape_key_remove(obj.data.shape_keys.key_blocks["__flat__"])

flatten_shape_keys(basemesh)
log("phenotype flattened into basis; shape keys now:",
    len(basemesh.data.shape_keys.key_blocks) if basemesh.data.shape_keys else 0)

# Texture overrides from the prepare_textures.py pre-step (brand print, skin tint): repoint
# the matching Blender images at the edited copies so they embed into the GLB. Entries named
# <image>__normal are DERIVED NORMAL MAPS — wired below into whichever material uses <image>
# as its base color (the skin realism pass; MakeHuman skins ship diffuse-only).
overrides_path = Path(__file__).parent / "out" / "tex" / f"{CFG['name']}.overrides.json"
normal_maps = {}
if overrides_path.exists():
    overrides = json.loads(overrides_path.read_text())
    for key, path in overrides.items():
        if key.endswith('__normal'):
            normal_maps[key[:-len('__normal')]] = path
            continue
        for img in bpy.data.images:
            stem = Path(img.filepath).stem if img.filepath else img.name
            if key == stem or key == img.name:
                img.filepath = path
                img.reload()
                log("texture override:", key, "→", Path(path).name)

def wire_normal_maps():
    """Attach derived normal maps: find the material whose Base Color image matches the
    manifest key, add Image Texture (Non-Color) → Normal Map → BSDF Normal. Runs after
    materials exist; the glTF exporter turns this into normalTexture."""
    for base_key, npath in normal_maps.items():
        for mat in bpy.data.materials:
            if not mat.use_nodes:
                continue
            bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if not bsdf:
                continue
            base_in = bsdf.inputs.get('Base Color')
            if not (base_in and base_in.links):
                continue
            src_node = base_in.links[0].from_node
            img = getattr(src_node, 'image', None)
            stem = Path(img.filepath).stem if (img and img.filepath) else (img.name if img else '')
            if stem != base_key and (img and img.name) != base_key:
                continue
            nimg = bpy.data.images.load(npath)
            nimg.colorspace_settings.name = 'Non-Color'
            tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
            tex.image = nimg
            nm = mat.node_tree.nodes.new('ShaderNodeNormalMap')
            nm.inputs['Strength'].default_value = 1.0
            mat.node_tree.links.new(tex.outputs['Color'], nm.inputs['Color'])
            mat.node_tree.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
            log("normal map wired:", base_key, "→", mat.name)

# ---- 2) TalkingHead rig + weights -----------------------------------------------------------
log("loading talkinghead rig")
bpy.ops.object.select_all(action='DESELECT')
basemesh.select_set(True)
bpy.context.view_layer.objects.active = basemesh

rig = Rig.from_json_file_and_basemesh(str(VENDOR / "talkinghead.mpfbskel"), basemesh)
armature = rig.create_armature_and_fit_to_basemesh(for_developer=True)
basemesh.parent = armature
log("rig created:", armature.name, "bones:", len(armature.data.bones))

with open(VENDOR / "talkinghead.mhw") as f:
    mhw = json.load(f)
RigService.apply_weights([armature], basemesh, mhw, all=False, replace=True)
log("weights applied")

# Drop stray helper objects (Rig.create_armature… leaves a dev Icosphere) — anything that
# isn't the basemesh or a config asset must not ride into the GLB.
for obj in list(bpy.data.objects):
    if obj.type == 'MESH' and not obj.data.materials:
        log("removing junk mesh:", obj.name)
        bpy.data.objects.remove(obj, do_unlink=True)

# Rig the child meshes (clothes/hair/eyes/teeth). CRITICAL: MakeHuman assets bind to the
# BASEMESH (mhclo vertex mapping), not to bones — they carry no bone vertex groups of their
# own, so an armature modifier alone exports them as STATIC meshes (the glTF exporter skips
# the skin). That was the invisible root defect behind every in-app mangling: TalkingHead
# posed the skinned body while clothes/hair/eyes stayed frozen at rest.
#
# Weight source, in order of preference:
#   1. The asset's own mhclo BINDING (ClothesService.interpolate_weights): every garment
#      vert is tied to the 3 specific basemesh verts it was FITTED to, so a sleeve vert can
#      only ever inherit arm weights and a waistband vert hip weights. This is how MakeHuman
#      itself weights proxies, and it is immune to the A-pose proximity ambiguities below.
#   2. Fallback — proximity data_transfer. POLYINTERP_VNORPROJ (project along vertex
#      normals), NOT POLYINTERP_NEAREST: nearest-face is ambiguous at the A-pose armpit
#      (torso side and inner arm are equidistant) — sleeve verts grabbed torso weights and
#      the smart-casual shirts TORE at the shoulders the moment the arms dropped to idle.
def binding_weights(obj):
    try:
        path = ClothesService.find_clothes_absolute_path(obj)
        if not path:
            return False
        m = Mhclo()
        m.load(path)
        # The binding is positional (mhclo vert N ↔ object vert N) — only trust it while
        # the imported topology still matches 1:1 (we run before any decimation/bakes).
        if not m.verts or len(m.verts) != len(obj.data.vertices):
            log("mhclo binding mismatch:", obj.name, f"({len(m.verts or [])} vs {len(obj.data.vertices)} verts)")
            return False
        ClothesService.interpolate_weights(basemesh, obj, armature, m)
        return True
    except Exception as e:  # noqa: BLE001 — any failure just falls back to proximity
        log("mhclo binding failed:", obj.name, repr(e))
        return False

children = [o for o in bpy.data.objects if o.type == 'MESH' and o is not basemesh]
for obj in children:
    if binding_weights(obj):
        log("weights from mhclo binding:", obj.name)
    else:
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        basemesh.select_set(True)
        bpy.context.view_layer.objects.active = basemesh
        bpy.ops.object.data_transfer(
            use_create=True,
            data_type='VGROUP_WEIGHTS',
            layers_select_src='ALL',
            layers_select_dst='NAME',
            vert_mapping='POLYINTERP_VNORPROJ',
        )
        log("weights from proximity transfer:", obj.name)
    RigService.ensure_armature_modifier(obj, armature)
    if obj.parent is None or obj.parent is basemesh:
        obj.parent = armature

    # The A-pose proximity transfer contaminates garments that pass near the RESTING
    # HANDS (jean hips, jacket hems sit right next to them): those verts grab
    # Hand/finger weights and the garment tears into shards the moment TalkingHead
    # drops the arms to idle. No child asset should ever skin to hand bones — strip
    # the groups and renormalize what remains (hip verts fall back to their real
    # hip/leg weights, sleeve cuffs to the forearm).
    hand_groups = [g for g in obj.vertex_groups if "Hand" in g.name]
    if hand_groups:
        n = len(hand_groups)
        for g in hand_groups:
            obj.vertex_groups.remove(g)
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.vertex_group_normalize_all(lock_active=False)
        log("stripped hand/finger weights:", obj.name, f"({n} groups)")

    # Same failure, second bone family: pants/waistband verts also grab Arm/ForeArm
    # weights from the forearms hovering beside the hips, and shear sideways when the
    # arms drop to idle. Position alone can NOT separate pants from sleeves — a fitted
    # shirt's sleeves dip below the navel line and inside any hip-width cut, and the
    # first (position-only) version of this strip deleted their legitimate weights: the
    # sleeves froze in A-pose while the arms dropped out of them (the smart-casual
    # tearing). Discriminate by WEIGHT SHARE instead: contamination is always a minority
    # of a vert's total weight (a waistband vert is Hips/Leg-dominant), while real sleeve
    # verts are Arm-dominant — strip arm weights below the navel only where they are the
    # minority. Thresholds derive from bone landmarks so they hold at any unit scale.
    arm_groups = {g.index: g for g in obj.vertex_groups
                  if any(k in g.name for k in ("Arm", "Shoulder"))}
    if arm_groups and "Hips" in armature.data.bones and "Neck" in armature.data.bones:
        hips_z = (armature.matrix_world @ armature.data.bones["Hips"].head_local).z
        neck_z = (armature.matrix_world @ armature.data.bones["Neck"].head_local).z
        z_cut = hips_z + 0.25 * (neck_z - hips_z)      # ~navel line
        mw = obj.matrix_world
        strip = []
        for v in obj.data.vertices:
            co = mw @ v.co
            if co.z >= z_cut:
                continue
            arm_w = sum(ge.weight for ge in v.groups if ge.group in arm_groups)
            if arm_w <= 0.0:
                continue
            total_w = sum(ge.weight for ge in v.groups)
            if arm_w < 0.5 * total_w:                  # minority = contamination, not sleeve
                strip.append(v.index)
        if strip:
            for g in arm_groups.values():
                g.remove(strip)
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.vertex_group_normalize_all(lock_active=False)
            log("stripped minority arm weights below navel:", obj.name, f"({len(strip)} verts)")
log("child meshes rigged (weights transferred):", [o.name for o in children])

# Some MakeHuman-community assets are absurdly dense (a plain elvs tank is 58k verts —
# ~2.8 MB of exported geometry, tripling the GLB). Budget every child mesh: anything over
# CHILD_VERT_BUDGET collapses toward CHILD_VERT_TARGET. Plain cloth/hair survives this
# visually untouched; the basemesh (the morph carrier) is never decimated.
CHILD_VERT_BUDGET = 20000
CHILD_VERT_TARGET = 8000
for obj in children:
    n = len(obj.data.vertices)
    if n > CHILD_VERT_BUDGET:
        dec = obj.modifiers.new("Decimate", 'DECIMATE')
        dec.ratio = CHILD_VERT_TARGET / n
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=dec.name)
        log("decimated dense child:", obj.name, n, "→", len(obj.data.vertices), "verts")

# ---- 3) ARKit + Oculus viseme shape keys ----------------------------------------------------
import bl_ext.blender_org.mpfb as mpfb_module
face_meshes = [basemesh]
log("building ARKit + viseme targets (this is the slow step)")
th["build_targets"](mpfb_module, face_meshes, VENDOR / "talkinghead-targets.zip")
log("targets built:", len(basemesh.data.shape_keys.key_blocks) if basemesh.data.shape_keys else 0, "shape keys")

# ---- 4) Bake helpers/masks away while keeping shape keys ------------------------------------
bpy.ops.object.select_all(action='DESELECT')
basemesh.select_set(True)
bpy.context.view_layer.objects.active = basemesh

# OPTIONAL head/body subdivision, default OFF. The bake preserves shape keys and renders
# correctly in Blender, but the subdivided morphs come out ~5x WEAKER through the
# three.js/TalkingHead pipeline (measured in-app: talking/idle motion 1.1x vs 3.8x
# without subdiv) — visible lip-sync beats mesh density, and the derived normal maps
# carry the surface detail subdiv was for. Enable per-config only with in-app verification.
if CFG.get("subdiv", False):
    sub = basemesh.modifiers.new("Subdivision", 'SUBSURF')
    sub.levels = 1
    sub.render_levels = 1

mods = th["find_modifier_names"](basemesh, ["delete", "hide helpers", "subdivision"])
if mods:
    log("applying modifiers with shape keys:", mods)
    th["apply_modifiers"](bpy.context, mods)
    log("post-bake verts:", len(basemesh.data.vertices))

# ---- 5) Scale to meters + A-pose bone axes --------------------------------------------------
bpy.ops.object.select_all(action='DESELECT')
armature.select_set(True)
bpy.context.view_layer.objects.active = armature
th["scale_character"]([armature], "Hips", CFG.get("hips_height", 1.0))
th["fix_bone_axes"]([armature], th["BONE_AXES_DATA_A"])
log("scaled + bone axes fixed")

# Consistent outward normals on every mesh. The MPFB bake pipeline leaves patches of
# inward-facing normals; double-sided BLEND materials masked it, but with proper OPAQUE +
# backface culling those patches literally cull away (the face disappeared) and lighting
# was shading them dark all along. Recalc is safe with shape keys (normals aren't per-key,
# and morph normals aren't exported).
for obj in bpy.data.objects:
    if obj.type != 'MESH':
        continue
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode='OBJECT')
log("normals recalculated outward")

# Root must be named "Armature" for TalkingHead.
armature.name = "Armature"

# Apply all transforms everywhere.
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# ---- 6) glTF-safe materials ------------------------------------------------------------------
# The exporter derives alphaMode from the NODE GRAPH, not from material.blend_method (which
# Blender 4.5 deprecated — an earlier version of this pass set it and every material still
# exported as alphaMode BLEND + doubleSided, which renders in three.js as sorting artifacts,
# milky faces and blank-looking eyes). The real fix, per TalkingHead's MPFB.md:
#   OPAQUE  (skin/clothes/teeth/eyes): unlink whatever feeds Principled Alpha, force Alpha=1
#           → exporter writes alphaMode OPAQUE; backface culling → doubleSided false.
#   MASK    (hair/eyebrows/eyelashes): insert Math(GREATER_THAN, threshold) before Alpha
#           → exporter writes alphaMode MASK (alpha-clip cutout); keep doubleSided.
# Classify by the CONFIG's asset slugs, not by name guessing — the material is named after
# the asset folder (coach-male.short02), which says nothing about being hair.
def slug_of(fragment):
    return Path(fragment).stem.lower() if fragment else None

CUTOUT_SLUGS = tuple(s for s in (slug_of(CFG.get(k)) for k in ('hair', 'eyebrows', 'eyelashes')) if s)
EYE_SLUG = slug_of(CFG.get('eyes', 'low-poly/low-poly.mhclo'))

def principled_of(mat):
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None

for mat in bpy.data.materials:
    if not mat.use_nodes:
        continue
    bsdf = principled_of(mat)
    if not bsdf:
        continue
    lname = mat.name.lower()
    alpha_in = bsdf.inputs.get('Alpha')
    is_eye = bool(EYE_SLUG and EYE_SLUG in lname)

    if is_eye:
        # High-poly eyes are TWO-layer: a transparent cornea shell over the iris ball, split
        # by the texture's alpha. Severing alpha (the opaque pass) turns the shell opaque
        # white and blanks the eye — keep the alpha link so the exporter emits BLEND for
        # this one small contained mesh.
        pass
    elif any(s in lname for s in CUTOUT_SLUGS):
        # Cutout: route the existing alpha source through a hard threshold.
        if alpha_in and alpha_in.links:
            src = alpha_in.links[0].from_socket
            gt = mat.node_tree.nodes.new('ShaderNodeMath')
            gt.operation = 'GREATER_THAN'
            gt.inputs[1].default_value = 0.4
            mat.node_tree.links.remove(alpha_in.links[0])
            mat.node_tree.links.new(src, gt.inputs[0])
            mat.node_tree.links.new(gt.outputs[0], alpha_in)
        mat.use_backface_culling = False        # hair cards need both sides
        # Flat hair cards at default roughness mirror the lights as a gray sheen that
        # washes out even a near-black diffuse — matte them.
        r = bsdf.inputs.get('Roughness')
        if r and not r.links:
            r.default_value = 0.85
    else:
        # Opaque: sever every alpha path so the exporter can't infer BLEND.
        if alpha_in:
            for link in list(alpha_in.links):
                mat.node_tree.links.remove(link)
            alpha_in.default_value = 1.0
        mat.use_backface_culling = True         # exports doubleSided: false

    if is_eye:
        # Wet-glint eyes: low roughness + a clearcoat layer (the iris texture is already
        # wired; the old dull/milky look was BLEND + roughness 0.7).
        r = bsdf.inputs.get('Roughness')
        if r:
            r.default_value = 0.15
        cc = bsdf.inputs.get('Coat Weight') or bsdf.inputs.get('Clearcoat')
        if cc and not cc.links:
            cc.default_value = 0.5
    elif 'body' in lname or '.body' in lname or 'skin' in lname or CFG['name'] + '.body' == mat.name.lower():
        r = bsdf.inputs.get('Roughness')
        if r and not r.links:
            r.default_value = 0.5               # diffuse-only skin: realism lives in roughness
log("materials normalized (node-level alpha, 4.5-safe)")
wire_normal_maps()

# ---- 7) Export GLB ---------------------------------------------------------------------------
bpy.ops.object.select_all(action='SELECT')
OUT_GLB.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.export_scene.gltf(
    filepath=str(OUT_GLB),
    export_format='GLB',
    use_selection=True,
    export_animations=False,
    export_skins=True,
    export_morph=True,
    export_morph_normal=False,     # halves morph payload; TalkingHead doesn't need morph normals
    export_yup=True,
    export_apply=False,            # NEVER bake modifiers here — it would drop the shape keys
    export_image_format='AUTO',
)
log("exported", OUT_GLB, f"{OUT_GLB.stat().st_size/1e6:.1f} MB")

# ---- 8) Optional preview render --------------------------------------------------------------
# Rest pose only. Rest-pose previews HIDE skinning bugs (the smart-casual shirts shipped
# torn exactly this way) — the POSED gate lives in diag_pose.py, which make.sh runs on the
# FINAL exported GLB (bone axes there match what TalkingHead actually poses).
if PREVIEW:
    scene = bpy.context.scene
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (0.55, -3.4, 1.05)
    cam.rotation_euler = (math.radians(87), 0, math.radians(9))
    scene.camera = cam
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", 'SUN'))
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(55), 0, math.radians(25))
    sun.data.energy = 3.5
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
    scene.render.resolution_x = 540
    scene.render.resolution_y = 960
    scene.render.filepath = str(PREVIEW)
    bpy.ops.render.render(write_still=True)
    log("preview", PREVIEW)

log("DONE")
