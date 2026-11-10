"""Render neutral street views from a Blender file without saving it."""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


source, output, *poses = sys.argv[sys.argv.index("--") + 1:]
output = Path(output)
output.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=source)
scene = bpy.context.scene
scene.world = bpy.data.worlds.new("Scan background")
scene.world.use_nodes = True
background = next(node for node in scene.world.node_tree.nodes if node.type == "BACKGROUND")
background.inputs["Color"].default_value = (0.28, 0.35, 0.45, 1)
scene.view_settings.view_transform = "Standard"
scene.view_settings.exposure = 0
scene.view_settings.gamma = 1
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 3
scene.cycles.use_denoising = False
scene.render.resolution_x = 640
scene.render.resolution_y = 480
scene.render.resolution_percentage = 100
scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
scene.render.use_sequencer = False
scene.render.use_compositing = False

camera_data = bpy.data.cameras.new("Road scan")
camera_data.lens = 22
camera_data.clip_start = 0.01
camera = bpy.data.objects.new("Road scan", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera

records = []
for pose in poses:
    name, x, y, z, yaw, pitch, *lens = pose.split(",")
    panorama = lens == ["360"]
    camera_data.type = "PANO" if panorama else "PERSP"
    if panorama:
        camera_data.panorama_type = "EQUIRECTANGULAR"
        scene.render.resolution_x, scene.render.resolution_y = 2048, 1024
    else:
        scene.render.resolution_x, scene.render.resolution_y = 640, 480
    camera_data.lens = float(lens[0]) if lens and not panorama else 22
    camera.location = tuple(map(float, (x, y, z)))
    yaw, pitch = map(math.radians, map(float, (yaw, pitch)))
    forward = Vector((math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch)))
    camera.rotation_euler = forward.to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    scene.render.filepath = str(output / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    optics = {"coverage_degrees": [360, 180]} if panorama else {"lens_mm": camera_data.lens}
    records.append({"image": f"{name}.png", "projection": "equirectangular" if panorama else "perspective",
                    "resolution": [scene.render.resolution_x, scene.render.resolution_y], **optics,
                    "matrix_world": [list(row) for row in camera.matrix_world]})

manifest = output / "views.json"
temporary = manifest.with_suffix(".tmp")
temporary.write_text(json.dumps({"source": source, "poses": poses, "views": records}, indent=2), encoding="utf-8")
temporary.replace(manifest)
