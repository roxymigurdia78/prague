"""Render matched lighting comparisons from the four saved street worlds.

Run with Blender in background mode and pass script arguments after --.
Use --preview to choose cameras; supply each choice as --view WORLD=PLAN_INDEX,YAW.
"""
import argparse
import csv
import hashlib
from html import escape
import json
import math
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from streetlab.blender_presentation import configure_gpu
from streetlab.sun_sky import BACKGROUND_NODE, get_sky_settings, set_sky


WORLDS = {
    "prague1": ("プラハ1", "プラハ1.blend", "20261111_131502_714579"),
    "prague2": ("プラハ2", "プラハ2.blend", "20261111_131958_258762"),
    "vienna": ("ウィーン", "ウィーン.blend", "20261111_145256_905428"),
    "rothenburg": (
        "ローテンブルク", "空が終わっているファイルたち/ドイツ_ローテンブルク.blend",
        "20261110_083134_011934_rothenburg",
    ),
}
CASES = {
    "baseline": ("baseline", None),
    "exposure_low": ("exposure", -1),
    "exposure_high": ("exposure", 1),
    "balance_warm": ("white_balance", 7500),
    "balance_cool": ("white_balance", 5000),
    "contrast_low": ("contrast", "AgX - Medium Low Contrast"),
    "contrast_high": ("contrast", "AgX - Medium High Contrast"),
    "sun_low": ("sun_intensity", 0.5),
    "sun_high": ("sun_intensity", 2.0),
    "world_low": ("world_strength", 0.6),
    "world_high": ("world_strength", 1.6),
    "aerosols_low": ("aerosols", 0.75),
    "aerosols_high": ("aerosols", 1.25),
    "saturation_low": ("saturation", 0.75),
    "saturation_high": ("saturation", 1.25),
    "texture_low": ("texture_brightness", 0.75),
    "texture_high": ("texture_brightness", 1.2),
    "night_off": ("night", {"power": 0}),
    "night_on": ("night", {"power": 1}),
    "night_dim": ("night", {"power": 0.45}),
    "night_bright": ("night", {"power": 1.9}),
    "night_neutral": ("night", {"power": 1, "color": "neutral"}),
    "night_fixtures_only": ("night", {"power": 1, "windows": False}),
}
LABELS = {
    "baseline": "昼の基準",
    "exposure_low": "露出 −1 EV", "exposure_high": "露出 +1 EV",
    "balance_warm": "色温度 7500 K", "balance_cool": "色温度 5000 K",
    "contrast_low": "コントラスト 弱", "contrast_high": "コントラスト 強",
    "sun_low": "太陽光 ×0.5", "sun_high": "太陽光 ×2",
    "world_low": "ワールド光 ×0.6", "world_high": "ワールド光 ×1.6",
    "aerosols_low": "大気の粒子 ×0.75", "aerosols_high": "大気の粒子 ×1.25",
    "saturation_low": "彩度 ×0.75", "saturation_high": "彩度 ×1.25",
    "texture_low": "素材色の明るさ ×0.75", "texture_high": "素材色の明るさ ×1.2",
    "night_off": "夜・消灯", "night_on": "夜・点灯",
    "night_dim": "夜・光量 ×0.45", "night_bright": "夜・光量 ×1.9",
    "night_neutral": "夜・光を白寄りに", "night_fixtures_only": "夜・街灯とランタンのみ",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def open_scene(source):
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=True, use_scripts=False)
    return bpy.context.scene


def install_camera(scene, plan, plan_index, yaw):
    entry = plan[plan_index]
    camera = bpy.data.objects.get("Trial camera")
    if camera is None:
        camera_data = bpy.data.cameras.new("Trial camera")
        camera = bpy.data.objects.new("Trial camera", camera_data)
        scene.collection.objects.link(camera)
    else:
        camera_data = camera.data
    camera_data.lens = 24
    camera_data.clip_start = 0.01
    camera.location = entry["position"]
    heading = math.radians(yaw)
    direction = Vector((math.cos(heading), math.sin(heading), -math.tan(math.radians(3))))
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.region_3d.view_perspective = "CAMERA"
    bpy.context.view_layer.update()
    return {
        "plan_index": plan_index, "source_view": entry["camera"], "yaw_degrees": yaw,
        "pitch_degrees": -3, "position": list(camera.location), "lens_mm": camera_data.lens,
        "height_above_floor": entry.get("height_above_floor"),
    }


def prepare_render(scene, preview, cpu):
    scene.render.engine = "CYCLES"
    scene.render.resolution_x, scene.render.resolution_y = (384, 288) if preview else (768, 576)
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.film_transparent = False
    scene.render.use_sequencer = False
    scene.render.use_file_extension = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.color_management = "FOLLOW_SCENE"
    scene.cycles.samples = 8 if preview else 24
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = True
    scene.cycles.seed = 17
    if cpu:
        scene.cycles.device = "CPU"
        return {"device": "CPU"}
    return configure_gpu(scene)


def background(scene):
    return scene.world.node_tree.nodes[BACKGROUND_NODE]


def set_saturation(scene, factor):
    tree = scene.compositing_node_group
    if tree is None:
        tree = bpy.data.node_groups.new("Trial color saturation", "CompositorNodeTree")
        scene.compositing_node_group = tree
        tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        render_layers = tree.nodes.new("CompositorNodeRLayers")
        output = tree.nodes.new("NodeGroupOutput")
        render_layers.location.x = -300
        output.location.x = 300
        source = render_layers.outputs["Image"]
    else:
        output = next(node for node in tree.nodes if node.type == "GROUP_OUTPUT" and node.is_active_output)
        link = output.inputs["Image"].links[0]
        source = link.from_socket
        tree.links.remove(link)
    adjust = tree.nodes.new("CompositorNodeHueSat")
    adjust.label = "Trial saturation"
    adjust.inputs["Saturation"].default_value = factor
    tree.links.new(source, adjust.inputs["Image"])
    tree.links.new(adjust.outputs["Image"], output.inputs["Image"])
    scene.render.use_compositing = True


def set_texture_brightness(scene, factor):
    street = scene.objects["decimated_mesh"]
    names = []
    for material in street.data.materials:
        if not material or not material.node_tree:
            continue
        tree = material.node_tree
        for shader in [node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"]:
            color = shader.inputs["Base Color"]
            if not color.is_linked:
                color.default_value = (*[channel * factor for channel in color.default_value[:3]], color.default_value[3])
                names.append(material.name)
                continue
            link = color.links[0]
            source = link.from_socket
            tree.links.remove(link)
            gain = tree.nodes.new("ShaderNodeVectorMath")
            gain.operation = "SCALE"
            gain.label = "Trial base color multiplier"
            gain.location = (shader.location.x - 220, shader.location.y - 220)
            gain.inputs["Scale"].default_value = factor
            tree.links.new(source, gain.inputs[0])
            tree.links.new(gain.outputs["Vector"], color)
            names.append(material.name)
    if not names:
        raise ValueError("No street base color material was found")
    return sorted(set(names))


def place_emitters(scene, detections, camera_height, power, color, windows):
    camera = scene.camera
    eye = camera.location
    depsgraph = bpy.context.evaluated_depsgraph_get()
    scale = camera_height / 1.65
    candidates = []
    for record in detections:
        kind = record["kind"]
        if kind not in {"window", "streetlight", "lantern"} or record.get("mesh") != "decimated_mesh":
            continue
        point = Vector(record["position"])
        normal = Vector(record["normal"]).normalized()
        delta = point - eye
        distance = delta.length
        if distance < scale or distance > scale * 45 or not normal.length:
            continue
        if normal.dot(-delta) < 0:
            normal.negate()
        projection = world_to_camera_view(scene, camera, point)
        in_frame = projection.z > 0 and 0 < projection.x < 1 and 0 < projection.y < 1
        if kind == "window" and (not windows or not in_frame):
            continue
        hit, location, *_ = scene.ray_cast(depsgraph, eye, delta.normalized(), distance=distance)
        if hit and (point - location).length > scale * 0.45:
            continue
        candidates.append((not in_frame, distance, -record.get("confidence", 0), record, point, normal))

    collection = bpy.data.collections.new("Trial night emitters")
    scene.collection.children.link(collection)
    placements = []
    positions = {"window": [], "fixture": []}
    for outside, _, _, record, point, normal in sorted(candidates, key=lambda row: row[:3]):
        kind = record["kind"]
        group = "window" if kind == "window" else "fixture"
        existing = positions[group]
        if len(existing) >= 4 or any((point - other).length < scale * 0.6 for other in existing):
            continue
        lamp_type = "AREA" if group == "window" else "POINT"
        light = bpy.data.lights.new(record["id"], lamp_type)
        light.energy = (30 if group == "window" else 120) * scale**2 * power
        light.color = (1.0, 0.90, 0.78) if color == "neutral" else (1.0, 0.62, 0.32)
        obj = bpy.data.objects.new(f"Trial {record['id']}", light)
        collection.objects.link(obj)
        obj.location = point + normal * (scale * (0.16 if group == "window" else 0.27))
        if group == "window":
            light.shape = "RECTANGLE"
            light.size = min(scale * 2.5, max(scale * 0.2, record["width"] * 0.7))
            light.size_y = min(scale * 2.5, max(scale * 0.2, record["height"] * 0.7))
            obj.rotation_euler = normal.to_track_quat("-Z", "Y").to_euler()
        else:
            light.shadow_soft_size = scale * 0.11
        existing.append(point)
        placements.append({
            "id": record["id"], "kind": kind, "type": lamp_type, "position": list(obj.location),
            "anchor_position": list(point), "energy_w": light.energy, "color_linear": list(light.color),
            "in_frame": not outside,
        })
    if not placements:
        raise ValueError("No detected fixtures or windows could be placed at this camera")
    return placements


def apply_case(scene, control, value, detections, camera_height):
    view = scene.view_settings
    changes = {"control": control, "value": value}
    if control == "exposure":
        view.exposure += value
    elif control == "white_balance":
        view.use_white_balance = True
        view.white_balance_temperature = value
    elif control == "contrast":
        view.look = value
    elif control in {"sun_intensity", "aerosols"}:
        current = getattr(get_sky_settings(scene), control)
        set_sky(scene, **{control: current * value})
    elif control == "world_strength":
        background(scene).inputs["Strength"].default_value *= value
    elif control == "saturation":
        set_saturation(scene, value)
    elif control == "texture_brightness":
        changes["materials"] = set_texture_brightness(scene, value)
    elif control == "night":
        set_sky(scene, sun_disc=False)
        color = background(scene).inputs["Color"]
        for link in list(color.links):
            scene.world.node_tree.links.remove(link)
        color.default_value = (0.028, 0.038, 0.068, 1)
        background(scene).inputs["Strength"].default_value = 1
        view.exposure = 0
        changes["ambient_linear_rgba"] = list(color.default_value)
        changes["emitters"] = place_emitters(
            scene, detections, camera_height, value["power"],
            value.get("color", "warm"), value.get("windows", True),
        ) if value["power"] else []
    return changes


def render(scene, image):
    scene.render.filepath = str(image)
    result = bpy.ops.render.render(write_still=True)
    if "FINISHED" not in result or not image.is_file() or not image.stat().st_size:
        raise RuntimeError(f"Rendering did not write {image}")


def write_catalog(out):
    rows = []
    sections = []
    for key, (label, _, _) in WORLDS.items():
        cards = {"day": [], "night": []}
        for case, (control, value) in CASES.items():
            prefix = f"{key}/{case}"
            settings_path = out / prefix / "settings.json"
            if not settings_path.is_file():
                continue
            settings = read_json(settings_path)
            if settings["change"]["value"] != value:
                continue
            image = f"{prefix}/view.png"
            blend = f"{prefix}/scene.blend"
            title = escape(LABELS[case])
            baseline = f"{key}/{settings['comparison_baseline']}/view.png"
            rows.append({
                "world": key, "world_label": label, "case": case, "label": LABELS[case],
                "image": image, "baseline_image": baseline, "blend": blend,
                "settings": f"{prefix}/settings.json",
            })
            cards["night" if control == "night" else "day"].append(
                f'<article><a href="{image}"><img loading="lazy" src="{image}" alt="{title}"></a>'
                f'<div><strong>{title}</strong><nav><a href="{blend}">Blender</a>'
                f'<a href="{prefix}/settings.json">設定</a></nav></div></article>'
            )
        if cards["day"] or cards["night"]:
            groups = "".join(
                f'<h3>{heading}</h3><div class="grid">{"".join(cards[phase])}</div>'
                for phase, heading in (("day", "昼"), ("night", "夜")) if cards[phase]
            )
            sections.append(f'<section id="{key}"><h2>{escape(label)}</h2>{groups}</section>')

    if not rows:
        return
    with (out / "index.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    navigation = " · ".join(
        f'<a href="#{key}">{escape(label)}</a>' for key, (label, _, _) in WORLDS.items()
        if any(row["world"] == key for row in rows)
    )
    (out / "index.html").write_text(f"""<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>街並みの照明比較</title>
<style>
:root{{color-scheme:dark;font-family:Inter,"Yu Gothic UI",sans-serif;color:#f5f2eb;background:#181916}}
body{{max-width:1240px;margin:auto;padding:clamp(16px,4vw,54px)}}
h1{{font-size:2.3rem;margin:0 0 12px}} h2{{font-size:1.7rem;margin:0 0 16px}} h3{{margin:18px 0 10px}}
p{{line-height:1.65;color:#d4d1c9}} a{{color:#f2d89b}} section{{margin-top:40px;border-top:1px solid #3a3b33;padding-top:24px}}
.grid{{display:grid;gap:13px;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr))}}
article{{background:#262722;border:1px solid #3a3b33;border-radius:8px;overflow:hidden}}
article img{{display:block;width:100%;aspect-ratio:4/3;object-fit:contain;background:#0f100d}}
article div{{padding:9px 11px}} article nav{{display:flex;gap:12px;font-size:.88rem;margin-top:4px}}
</style>
<header><h1>街並みの照明比較</h1><nav>{navigation} · <a href="index.csv">画像と設定の一覧 CSV</a></nav>
<p>各街でカメラを固定し、昼は基準から設定を一つずつ変更しました。夜は薄い環境光の下で、既存の検出位置を使って点灯を試しました。画像を押すと元のPNGが開きます。</p></header>
<main>{"".join(sections)}</main></html>
""", encoding="utf-8")


def run(args):
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    selections = {}
    for override in args.view:
        key, selection = override.split("=", 1)
        index, yaw = selection.split(",", 1)
        selections[key] = (int(index), float(yaw))

    for key in args.world or WORLDS:
        label, filename, build = WORLDS[key]
        source = ROOT / "lighting_eval" / filename
        source_digest = file_hash(source)
        result_path = ROOT / "outputs/builds" / build / "result.json"
        result = read_json(result_path)
        plan = result["camera_plan_views"]
        if args.preview:
            scene = open_scene(source)
            prepare_render(scene, True, args.cpu)
            directory = out / "previews" / key
            directory.mkdir(parents=True, exist_ok=True)
            for index in (0, 3) if key == "rothenburg" else (0,):
                for yaw in range(0, 360, 90):
                    install_camera(scene, plan, index, yaw)
                    image = directory / f"{index:02d}_{yaw:03d}.png"
                    if not image.exists():
                        render(scene, image)
            continue

        if key not in selections:
            raise ValueError(f"Supply --view {key}=PLAN_INDEX,YAW after reviewing the previews")
        index, yaw = selections[key]
        inventory_path = Path(result["inventory_file"])
        detections = read_json(inventory_path)["objects"]
        for case in args.case or CASES:
            directory = out / key / case
            directory.mkdir(parents=True, exist_ok=True)
            blend, image, metadata = [directory / name for name in ("scene.blend", "view.png", "settings.json")]
            control, value = CASES[case]
            if all(path.is_file() and path.stat().st_size for path in (blend, image, metadata)):
                saved = read_json(metadata)
                camera = saved["camera"]
                if saved["source_sha256"] != source_digest or (camera["plan_index"], camera["yaw_degrees"]) != (index, yaw):
                    raise ValueError(f"Source or camera changed for existing case {directory}; use a new output")
                if (saved["change"]["control"], saved["change"]["value"]) == (control, value):
                    print(f"TRIAL REUSE {key} {case}", flush=True)
                    continue
                metadata.unlink()

            print(f"TRIAL START {key} {case}", flush=True)
            scene = open_scene(source)
            camera = install_camera(scene, plan, index, yaw)
            device = prepare_render(scene, False, args.cpu)
            changes = apply_case(scene, control, value, detections, camera["height_above_floor"])
            view = scene.view_settings
            settings = {
                "world": key, "world_label": label, "case": case,
                "comparison_baseline": (
                    {"night_off": "night_off", "night_on": "night_off"}.get(case, "night_on")
                    if control == "night" else "baseline"
                ),
                "source": str(source), "source_sha256": source_digest,
                "build_record": str(result_path), "inventory_file": str(inventory_path),
                "source_local_datetime": result["sun"]["local_datetime"],
                "lighting_setup": "synthetic_night_ambient" if control == "night" else "saved_daylight",
                "camera": camera, "change": changes, "blender_version": bpy.app.version_string,
                "render_device": device, "resolution": [768, 576], "samples": scene.cycles.samples,
                "color": {
                    "view_transform": view.view_transform, "look": view.look, "exposure_ev": view.exposure,
                    "white_balance_enabled": view.use_white_balance,
                    "temperature_k": view.white_balance_temperature, "tint": view.white_balance_tint,
                },
                "sky": get_sky_settings(scene).to_dict(),
                "world_strength": background(scene).inputs["Strength"].default_value,
                "image": "view.png", "blend": "scene.blend",
            }
            scene["streetlab_lighting_trial"] = json.dumps(settings, ensure_ascii=False)
            scene.render.filepath = "//view.png"
            bpy.context.preferences.filepaths.save_version = 0
            bpy.context.preferences.filepaths.file_preview_type = "NONE"
            bpy.ops.wm.save_as_mainfile(
                filepath=str(blend), compress=True, copy=True, relative_remap=False,
            )
            render(scene, image)
            temporary = metadata.with_suffix(".tmp")
            temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(metadata)
            print(f"TRIAL DONE {key} {case}", flush=True)

        if file_hash(source) != source_digest:
            raise RuntimeError(f"Original file changed during the batch: {source}")

    if not args.preview:
        write_catalog(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--world", action="append", choices=WORLDS)
    parser.add_argument("--view", action="append", default=[])
    parser.add_argument("--case", action="append", choices=CASES)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    run(parser.parse_args(sys.argv[sys.argv.index("--") + 1:]))

