# Posed skinning diagnostic — the gate that catches garment weight-transfer bugs BEFORE
# a coach ships. Rest-pose previews hide them: the A-pose weight transfer can put garment
# verts on the wrong bone, and the cloth only tears once TalkingHead drops the arms to the
# idle stance (the smart-casual shirts shipped torn exactly this way, July 2026).
#
# Imports the FINAL GLB (post gltf-transform — the exact file the app loads), rotates both
# arms down ~50°, renders front view. VIEW the output before shipping; shards/ballooning at
# the shoulders = broken skinning.
#
#   blender --background --python diag_pose.py -- out/<name>.glb out/<name>-posed.png
import bpy
import math
import sys
from pathlib import Path

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(ARGS) < 2:
    raise SystemExit("usage: blender --background --python diag_pose.py -- <in.glb> <out.png>")
GLB, OUT = Path(ARGS[0]), Path(ARGS[1])

def log(*a):
    print("[diag]", *a, flush=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(GLB))

armature = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
if armature is None:
    raise SystemExit("FATAL: no armature in " + str(GLB))

# On the imported (glTF-axes) rig, the Arm bones' local +X axis aligns with the world
# front-back axis (probed: LeftArm X≈world+Y, RightArm X≈world−Y), so rotating about
# local +X swings the arm in the FRONTAL plane — positive drops the arm toward the body,
# negative raises it, matching the app's idle drop and wave gesture. (Local Z swings the
# arm forward — zombie pose — not this.)
def pose_arms(left_deg, right_deg):
    bpy.ops.object.select_all(action='DESELECT')
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='POSE')
    for bone_name, deg in (("LeftArm", left_deg), ("RightArm", right_deg)):
        pb = armature.pose.bones.get(bone_name)
        if pb is None:
            log("no bone", bone_name, "— skipped")
            continue
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = (math.radians(deg), 0.0, 0.0)
    bpy.ops.object.mode_set(mode='OBJECT')

# Front camera + sun, mirroring generate_coach.py's preview framing.
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

# Render 1 — idle: both arms dropped (the app's resting stance).
pose_arms(50, 50)
scene.render.filepath = str(OUT)
bpy.ops.render.render(write_still=True)
log("posed render (idle)", OUT)

# Render 2 — wave: left arm raised, right arm down (the greet gesture stresses the armpit
# in the OTHER direction; the smart-casual shirts also tore while waving).
wave = OUT.with_name(OUT.stem + "-wave" + OUT.suffix)
pose_arms(-45, 50)
scene.render.filepath = str(wave)
bpy.ops.render.render(write_still=True)
log("posed render (wave)", wave)
