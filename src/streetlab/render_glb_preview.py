"""Render a downloaded GLB in background Blender, without changing the source file."""
import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector

parser = argparse.ArgumentParser()
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--output-dir", type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.output_dir.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
points = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
minimum = Vector(tuple(min(p[i] for p in points) for i in range(3)))
maximum = Vector(tuple(max(p[i] for p in points) for i in range(3)))
center = (minimum + maximum) / 2
span = max(maximum - minimum)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 20
scene.cycles.use_denoising = True
scene.render.resolution_x = 768
scene.render.resolution_y = 768
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
world = bpy.data.worlds.new("Preview world")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.16, 0.16, 0.16, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
scene.world = world


def light(name, offset, energy):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy * span * span
    data.shape = "DISK"
    data.size = span * 1.5
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = center + Vector(offset) * span
    obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()


light("Front softbox", (-1.5, -2, 2), 180)
light("Back softbox", (1.5, 2, 2), 180)
light("Fill", (2, -0.4, 0.7), 75)
camera_data = bpy.data.cameras.new("Preview camera")
camera_data.type = "ORTHO"
camera_data.ortho_scale = span * 1.16
camera = bpy.data.objects.new("Preview camera", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera
for name, direction in (("front", (0.08, -3, 0.12)), ("back", (-0.08, 3, 0.12))):
    camera.location = center + Vector(direction) * span
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str((args.output_dir / (name + ".png")).resolve())
    bpy.ops.render.render(write_still=True)
summary = {"meshes": len(meshes), "materials": len(bpy.data.materials),
           "bounds_min": list(minimum), "bounds_max": list(maximum)}
(args.output_dir / "scene_info.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
bpy.ops.file.pack_all()
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.region_3d.view_perspective = "CAMERA"
            area.spaces.active.shading.type = "MATERIAL"
bpy.ops.wm.save_as_mainfile(filepath=str((args.output_dir / "door_preview.blend").resolve()))
