"""Turn confirmed scene-inventory anchors into a camera-independent lighting layout."""
from hashlib import sha256
import json
import math
from pathlib import Path


KINDS = {'streetlight': 'fixture', 'lantern': 'fixture', 'window': 'window'}


def load_anchors(path):
    path = Path(path)
    inventory = json.loads(path.read_text(encoding='utf-8-sig'))
    return normalize_anchors(inventory['objects'], inventory.get('unit'), path)


def load_legacy_anchors(folder, source):
    folder = Path(folder).resolve(strict=True)
    job = json.loads((folder / 'job.json').read_text(encoding='utf-8-sig'))
    with Path(source).open('rb') as stream:
        digest = sha256()
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    if digest.hexdigest() != job['sha256']:
        raise ValueError('旧配置ループと道路撮影は、同じ元の街のBlenderファイルが必要です。')
    result = json.loads((folder / 'result.json').read_text(encoding='utf-8-sig'))
    recorded = Path(result['last_plan'])
    # The project and its outputs may have been moved as a unit.
    plan_path = folder / recorded.parent.name / recorded.name
    plan = json.loads(plan_path.read_text(encoding='utf-8-sig'))
    enabled = [obj | {'status': 'supported', 'width': obj['size'], 'height': obj['size']}
               for obj in plan['lights'] if obj['enabled']]
    anchors, unit = normalize_anchors(enabled, plan['unit'], plan_path)
    factors = {obj['id']: obj['energy_factor'] for obj in enabled}
    for anchor in anchors:
        factor = factors[anchor['id']]
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError(f'旧配置ループの光量が無効です: {anchor["id"]}')
        anchor['energy_factor'] = factor
    return anchors, unit, plan_path


def normalize_anchors(objects, unit, path):
    anchors = []
    seen = set()
    for obj in objects:
        if obj.get('status') != 'supported' or obj.get('kind') not in KINDS:
            continue
        key = obj['id']
        vectors = [obj['position'], obj['normal']]
        if (key in seen or any(len(v) != 3 or not all(math.isfinite(n) for n in v) for v in vectors)
                or not math.hypot(*obj['normal']) or not all(
                    math.isfinite(obj[k]) and obj[k] > 0 for k in ('width', 'height'))):
            raise ValueError(f'無効な照明配置情報: {key}')
        seen.add(key)
        anchor = {k: obj[k] for k in ('id', 'kind', 'position', 'normal', 'width', 'height')}
        corners = obj.get('corners')
        if corners and len(corners) == 4 and all(
                len(v) == 3 and all(math.isfinite(n) for n in v) for v in corners):
            anchor['corners'] = corners
        anchors.append(anchor)
    if not anchors:
        return [], None
    if unit is None:
        scan = path.parent / 'round_00' / 'views.json'
        unit = json.loads(scan.read_text(encoding='utf-8-sig'))['unit']
    if not math.isfinite(unit) or unit <= 0:
        raise ValueError('照明配置の縮尺が無効です。')
    # A fixed order gives fractional switching stable fixture IDs across rounds.
    anchors.sort(key=lambda obj: sha256(obj['id'].encode()).digest())
    return anchors, unit


def selected_anchors(anchors, knobs):
    selected = []
    for group in ('fixture', 'window'):
        fraction = knobs.get(group + '_fraction', 0)
        if fraction > 0:
            options = [obj for obj in anchors if KINDS[obj['kind']] == group]
            if options:
                shift = math.floor(len(options) * knobs.get(group + '_selection', 0)) % len(options)
                options = options[shift:] + options[:shift]
            selected.extend(options[:max(1, math.floor(len(options) * fraction + .5))])
    return selected


def warm_color(warmth):
    # Interpolate between the warm and neutral linear RGB presets of the night trials.
    return (1.0, .90 - .28 * warmth, .78 - .46 * warmth)


def add_window_panel(collection, name, anchor, center, normal, unit, color, power):
    import bpy
    from mathutils import Vector

    corners = anchor.get('corners')
    if not corners:
        return
    points = [center + (Vector(point) - center) * .76 + normal * (unit * .015) for point in corners]
    if (points[1] - points[0]).cross(points[2] - points[0]).dot(normal) < 0:
        points.reverse()
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(points, [], [(0, 1, 2, 3)])
    panel = bpy.data.objects.new(name, mesh)
    collection.objects.link(panel)
    # The area lamp supplies the illumination. The panel only shows lit glass.
    panel.visible_shadow = False
    panel.visible_diffuse = False
    panel.visible_glossy = False
    panel.visible_transmission = False
    panel.visible_volume_scatter = False
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    glow = nodes.new('ShaderNodeEmission')
    glow.inputs['Color'].default_value = (*color, 1)
    glow.inputs['Strength'].default_value = .9 * power
    output = nodes.new('ShaderNodeOutputMaterial')
    material.node_tree.links.new(glow.outputs[0], output.inputs['Surface'])
    mesh.materials.append(material)


def place_lights(scene, anchors, unit, knobs):
    import bpy
    from mathutils import Matrix, Vector

    selected = selected_anchors(anchors, knobs)
    if not selected:
        return []
    collection = bpy.data.collections.new('Lighting search fixtures')
    scene.collection.children.link(collection)
    placed = []
    for anchor in selected:
        group = KINDS[anchor['kind']]
        window = group == 'window'
        point = Vector(anchor['position'])
        normal = Vector(anchor['normal']).normalized()
        size = max(anchor['width'], anchor['height'])
        base_offset = max(unit * .08, size * .12)
        offset = base_offset * knobs[group + '_offset']
        name = 'Lighting search ' + anchor['id']
        lamp = bpy.data.lights.new(name, 'AREA' if window else 'POINT')
        power = knobs[group + '_power']
        lamp.color = warm_color(knobs[group + '_warmth'])
        if 'energy_factor' in anchor:
            lamp.energy = (60 if window else 90) * unit**2 * anchor['energy_factor'] * power
        else:
            lamp.energy = (30 if window else 120) * (unit / 1.65) ** 2 * power
        obj = bpy.data.objects.new(name, lamp)
        obj['inventory_id'] = anchor['id']
        collection.objects.link(obj)
        obj.location = point + normal * offset
        if window:
            lamp.shape = 'RECTANGLE'
            lamp.size = min(unit * 2.5, max(unit * .12, anchor['width'] * .76))
            lamp.size_y = min(unit * 2.5, max(unit * .12, anchor['height'] * .76))
            corners = anchor.get('corners')
            if corners:
                right = Vector(corners[1]) - Vector(corners[0])
                right -= normal * right.dot(normal)
            else:
                right = normal.orthogonal()
            if right.length_squared < 1e-12:
                right = normal.orthogonal()
            right.normalize()
            up = (-normal).cross(right)
            obj.rotation_euler = Matrix((right, up, -normal)).transposed().to_euler()
            add_window_panel(collection, name + ' glass', anchor, point, normal, unit, lamp.color, power)
        else:
            lamp.shadow_soft_size = max(unit * .04, min(unit * .2, size * .25))
        placed.append({'id': anchor['id'], 'kind': anchor['kind'], 'type': lamp.type,
                       'position': list(obj.location), 'anchor': anchor['position'],
                       'outward_normal': anchor['normal'], 'offset': offset,
                       'energy_w': lamp.energy, 'color_linear': list(lamp.color)})
    return placed
