"""Run in Blender. Project road observations and validate the traversable grid."""

import json
import sys
from collections import defaultdict
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector


def hit_floor(scene, graph, x, y, z, distance):
    hit, point, normal, *_ = scene.ray_cast(graph, (x, y, z), (0, 0, -1), distance=distance)
    return point if hit and normal.z >= 0.65 else None


def project_views(scene, graph, request, votes):
    observations = []
    step = request["step"]
    for view in request["views"]:
        pose = Matrix(view["matrix_world"])
        origin = pose.translation
        image = bpy.data.images.load(view["mask"], check_existing=False)
        width, height = image.size
        rgba = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(rgba)
        mask = rgba.reshape(height, width, 4)[::-1, :, 0] > 0.5
        bpy.data.images.remove(image)
        half_width = 18 / view["lens_mm"]
        half_height = half_width * height / width

        def project(x, y):
            direction = pose.to_3x3() @ Vector(((2 * x - 1) * half_width, (1 - 2 * y) * half_height, -1))
            hit, point, normal, *_ = scene.ray_cast(graph, origin, direction.normalized(), distance=18)
            if hit and normal.z >= 0.65 and point.z <= origin.z - request["eye"] * 0.35:
                return point
            return None

        for row, col in zip(*np.where(mask[::4, ::4])):
            point = project((col * 4 + 0.5) / width, (row * 4 + 0.5) / height)
            if point is not None:
                cell = (round(point.x / step), round(point.y / step))
                votes[cell].append(point.z)

        rows, cols = np.where(mask[::3, ::3])
        xs, ys = (cols * 3 + 0.5) / width, (rows * 3 + 0.5) / height
        for passage in view["passages"]:
            x, y = (value / 1000 for value in passage["point"])
            projected = None
            if xs.size:
                distances = (xs - x) ** 2 + (ys - y) ** 2
                for index in np.argsort(distances)[:18]:
                    if distances[index] > 0.08 ** 2:
                        break
                    projected = project(xs[index], ys[index])
                    if projected is not None:
                        break
            observations.append({**passage, "view": view["id"], "camera": list(origin),
                                 "position": list(projected) if projected is not None else None})
    return observations


def build_grid(scene, graph, votes, step, eye):
    cells = {}
    for (i, j), heights in votes.items():
        z = float(np.median(heights))
        point = hit_floor(scene, graph, i * step, j * step, z + 0.14, 0.28)
        if point is None:
            continue
        head_hit, *_ = scene.ray_cast(graph, point + Vector((0, 0, 0.05)), (0, 0, 1), distance=eye + 0.1)
        if not head_hit:
            cells[i, j] = point.z

    def connected(a, b):
        start = Vector((a[0] * step, a[1] * step, cells[a]))
        end = Vector((b[0] * step, b[1] * step, cells[b]))
        if abs(start.z - end.z) > step * 0.7:
            return False
        middle = start.lerp(end, 0.5)
        if hit_floor(scene, graph, middle.x, middle.y, middle.z + 0.14, 0.28) is None:
            return False
        delta = end - start
        for height in (0.2, eye):
            blocked, *_ = scene.ray_cast(graph, start + Vector((0, 0, height)), delta.normalized(), distance=delta.length)
            if blocked:
                return False
        return True

    links = [[list(a), list(b)] for a in sorted(cells) for b in ((a[0] + 1, a[1]), (a[0], a[1] + 1))
             if b in cells and connected(a, b)]
    return [[i, j, z] for (i, j), z in sorted(cells.items())], links


request_path, result_path = map(Path, sys.argv[sys.argv.index("--") + 1:])
request = json.loads(request_path.read_text(encoding="utf-8"))
bpy.ops.wm.open_mainfile(filepath=request["source"])
scene = bpy.context.scene
graph = bpy.context.evaluated_depsgraph_get()
if request["mode"] == "seed":
    x, y, z = request["start"]
    point = hit_floor(scene, graph, x, y, z, 5)
    if point is None:
        raise ValueError("Starting position has no walkable floor underneath. Supply --start X Y Z.")
    result = {"floor": list(point), "eye": [point.x, point.y, point.z + request["eye"]]}
else:
    votes = defaultdict(list, {(i, j): [z] for i, j, z in request["previous"]})
    observations = project_views(scene, graph, request, votes)
    if not request["previous"] and votes:
        # The road directly below the starting camera is outside its forward views.
        origin = Matrix(request["views"][0]["matrix_world"]).translation
        step = request["step"]
        radius = request["eye"] * 1.25
        span = int(np.ceil(radius / step))
        i0, j0 = round(origin.x / step), round(origin.y / step)
        for i in range(i0 - span, i0 + span + 1):
            for j in range(j0 - span, j0 + span + 1):
                if (i, j) in votes or np.hypot(i * step - origin.x, j * step - origin.y) > radius:
                    continue
                point = hit_floor(scene, graph, i * step, j * step, origin.z, request["eye"] + 0.18)
                if point is not None and abs(point.z - (origin.z - request["eye"])) <= 0.18:
                    votes[i, j].append(point.z)
    cells, links = build_grid(scene, graph, votes, request["step"], request["eye"])
    result = {"cells": cells, "links": links, "passages": observations}

result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
