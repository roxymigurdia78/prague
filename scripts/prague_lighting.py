"""Run in Blender to add the Prague sidebar, or call light('夕焼け') / apply_conversation() from its console.

apply_conversation() reads the newest outputs/conversations/*/scene_conditions.json written by
`run.py conversation` (place, season, time of day chosen from the conversation) and sets the sun and sky.
"""

from datetime import datetime
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector


def find_project():
    # Blender's Text Editor can set __file__ to <scene.blend>/<text name>.
    origins = [Path(__file__).absolute()]
    if bpy.data.filepath:
        origins.insert(0, Path(bpy.data.filepath))
    for origin in origins:
        for folder in origin.parents:
            if (folder / 'run.py').is_file() and (folder / 'src/streetlab/daylight.py').is_file():
                return folder.resolve()
    raise RuntimeError('Open the prepared scene inside the prague project, then run prague/scripts/prague_lighting.py')


PROJECT = find_project()
DATA = PROJECT / 'outputs/prague_daylight'
REFERENCE = DATA / 'eac4-prague-2025-07-15.json'
ROAD_CAPTURE = DATA / 'camera.json'
FIXTURE_SCAN = PROJECT / 'outputs/scene_inventory/20261104_201512_364149'
FIXTURE_ID = '_research_night_fixture'
sys.path.insert(0, str(PROJECT / 'src'))
from streetlab.daylight import NIGHT_PRESETS, set_world_time
from streetlab.sun_sky import set_sky, set_sun

CONVERSATIONS = PROJECT / 'outputs/conversations'
APPLIED_KEY = '_research_conversation_conditions'
WATCH_KEY = 'prague_conversation_watch'


def current_reference(scene=None):
    scene=bpy.context.scene if scene is None else scene
    return Path(scene.get('streetlab_atmosphere_reference_file',str(REFERENCE)))


def current_plan_path(scene=None):
    scene=bpy.context.scene if scene is None else scene
    return Path(scene.get('streetlab_camera_plan_file',str(ROAD_CAPTURE)))


def current_plan(scene=None):
    return json.loads(current_plan_path(scene).read_text(encoding='utf-8'))


def ensure_camera(scene, plan):
    if scene.camera is not None:
        return
    compatible=[PROJECT/plan['source_blend']]+[Path(p) for p in plan.get('compatible_blends',[])]
    if not bpy.data.filepath or Path(bpy.data.filepath).resolve() not in {p.resolve() for p in compatible}:
        raise ValueError('Set a scene camera or open the prepared Prague street_relight.blend')
    data = bpy.data.cameras.new('Prague road view')
    data.lens = 22
    data.clip_start = 0.01
    camera = bpy.data.objects.new('Prague road view', data)
    scene.collection.objects.link(camera)
    camera.matrix_world = Matrix(plan['matrix_world'])
    scene.camera = camera
    scene.render.resolution_x, scene.render.resolution_y = 1024, 768
    scene.render.resolution_percentage = 100
    scene.cycles.samples = 32
    scene.cycles.use_denoising = True


def enable_cycles_gpu(scene):
    preferences = bpy.context.preferences.addons['cycles'].preferences
    backend = preferences.compute_device_type
    if backend == 'NONE' or not any(device.type == backend and device.use
                                    for device in preferences.get_devices_for_type(backend)):
        raise RuntimeError('Enable a Cycles GPU in Blender Preferences > System first')
    scene.cycles.device = 'GPU'


def render_current(plan=None):
    if bpy.app.is_job_running('RENDER'):
        raise RuntimeError('Wait for the current render')
    if plan is None:
        plan = current_plan()
    scene = bpy.context.scene
    enable_cycles_gpu(scene)
    ensure_camera(scene, plan)
    mode = 'EXEC_DEFAULT' if bpy.app.background else 'INVOKE_DEFAULT'
    if 'CANCELLED' in bpy.ops.render.render(mode):
        raise RuntimeError('Blender cancelled the render. See the render report.')


def update_night_lights(scene, plan, enabled):
    lamps = {obj[FIXTURE_ID]: obj for obj in scene.objects if obj.type == 'LIGHT' and FIXTURE_ID in obj}
    inventory_file=plan.get('fixture_inventory',str(FIXTURE_SCAN/'inventory.json'))
    if enabled and inventory_file and bpy.data.filepath and (
            Path(bpy.data.filepath).resolve() == (PROJECT / plan['source_blend']).resolve()):
        inventory_path=Path(inventory_file)
        inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
        unit = json.loads((inventory_path.parent/'round_00/views.json').read_text(encoding='utf-8'))['unit']
        for fixture in inventory['objects']:
            if (fixture['id'] in lamps or fixture['status'] != 'supported'
                    or fixture['kind'] not in {'lantern', 'streetlight'}):
                continue
            name = 'Prague night ' + fixture['id']
            size = max(fixture['width'], fixture['height'])
            data = bpy.data.lights.new(name, 'POINT')
            data.color = (1, .64, .32)
            data.shadow_soft_size = max(unit * .04, min(unit * .2, size * .25))
            obj = bpy.data.objects.new(name, data)
            obj[FIXTURE_ID] = fixture['id']
            scene.collection.objects.link(obj)
            obj.location = Vector(fixture['position']) + Vector(fixture['normal']) * max(unit * .08, size * .12)
            lamps[fixture['id']] = obj
    for obj in lamps.values():
        if 'research_initial_power_w' not in obj.data:
            obj.data['research_initial_power_w'] = 0.0
        obj.data.energy = obj.data['research_initial_power_w'] if enabled else 0


def show_rendered():
    if bpy.context.screen is None:
        return
    for area in bpy.context.screen.areas:
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                space.shading.use_scene_world_render = True
                space.shading.use_scene_lights_render = True
                space.shading.type = 'RENDERED'
                area.tag_redraw()


def latest_conditions():
    files = list(CONVERSATIONS.glob('*/scene_conditions.json'))
    return max(files, key=lambda path: path.stat().st_mtime_ns) if files else None


def applied_conversation(scene=None):
    scene = bpy.context.scene if scene is None else scene
    try:
        return json.loads(scene.get(APPLIED_KEY, '{}'))
    except ValueError:
        return {}


def apply_conversation(path=None, *, preview=True):
    """Set the sun/sky of the open scene from a conversation's place, date and time."""
    if bpy.app.is_job_running('RENDER'):
        raise RuntimeError('Wait for the current render before applying conversation conditions')
    path = Path(path) if path else latest_conditions()
    if path is None:
        raise ValueError('No conversation conditions yet (outputs/conversations/*/scene_conditions.json)')
    stamp = f'{path.resolve()}|{path.stat().st_mtime_ns}'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('schema') != 'streetlab/scene-conditions/v1':
        raise ValueError(f'Not a conversation scene-conditions file: {path}')
    scene = bpy.context.scene
    if any(obj.type == 'LIGHT' and obj.data.type == 'SUN' and obj.data.energy > 0
           and not obj.hide_render for obj in scene.objects):
        raise ValueError('Disable the existing SUN light to use the sky Sun Disc')
    location = data['location']
    when = datetime.fromisoformat(data['local_datetime'])
    elevation = location.get('elevation')
    night = data['time_of_day'] in NIGHT_PRESETS
    sun = set_sun(location['latitude'], location['longitude'], when.date(), when.timetz().isoformat(),
                  scene=scene, north_seed=0,
                  altitude=None if elevation is None else max(0.0, float(elevation)))
    set_sky(scene, sun_disc=not night)
    scene.render.engine = 'CYCLES'
    if current_plan_path(scene).is_file():
        update_night_lights(scene, current_plan(scene), night)
    solar = sun['solar_position']
    record = {'stamp': stamp, 'file': str(path), 'id': data.get('id'),
              'conversation_id': data.get('conversation_id'),
              'place': (data.get('place') or {}).get('place_name') or location.get('name'),
              'location': location.get('name'), 'season': data['season'], 'time_of_day': data['time_of_day'],
              'local_datetime': data['local_datetime'], 'elevation_deg': solar['elevation_deg'],
              'azimuth_deg': solar['azimuth_deg']}
    scene[APPLIED_KEY] = json.dumps(record, ensure_ascii=False, allow_nan=False)
    if preview:
        show_rendered()
    print(f"conversation | {record['place']} | {record['season']} {record['time_of_day']} | "
          f"{record['local_datetime']} | elevation {solar['elevation_deg']:.2f}° | azimuth {solar['azimuth_deg']:.2f}°")
    return record


def _watch_conversations():
    """Timer: apply each newly written conversation while the scene's auto toggle is on."""
    scene = bpy.context.scene
    if scene is None or not getattr(scene, 'prague_auto_conversation', False):
        return None
    if bpy.app.is_job_running('RENDER'):
        return 3.0
    path = latest_conditions()
    if path is not None:
        stamp = f'{path.resolve()}|{path.stat().st_mtime_ns}'
        failed = bpy.app.driver_namespace.get(WATCH_KEY + '_failed')
        if stamp != applied_conversation(scene).get('stamp') and stamp != failed:
            try:
                apply_conversation(path)
            except Exception as error:  # Keep watching; report once per file version.
                bpy.app.driver_namespace[WATCH_KEY + '_failed'] = stamp
                print(f'conversation apply failed: {error}')
    return 3.0


def _start_watch():
    previous = bpy.app.driver_namespace.get(WATCH_KEY)
    if previous is not None and bpy.app.timers.is_registered(previous):
        bpy.app.timers.unregister(previous)
    bpy.app.driver_namespace[WATCH_KEY] = _watch_conversations
    bpy.app.timers.register(_watch_conversations, first_interval=0.5, persistent=True)


def _toggle_watch(self, context):
    if self.prague_auto_conversation:
        _start_watch()


def light(period, *, render=False, preview=True):
    if bpy.app.is_job_running('RENDER'):
        raise RuntimeError('Wait for the current render before changing the time of day')
    plan = current_plan()
    result = set_world_time(period, current_reference(), altitude=plan['altitude_m'])
    enable_cycles_gpu(bpy.context.scene)
    update_night_lights(bpy.context.scene, plan, result['period'] in NIGHT_PRESETS)
    if preview and not render:
        show_rendered()
    sun = result['sun']
    solar = sun['solar_position']
    print(f"{period} | {sun['local_datetime']} | elevation {solar['elevation_deg']:.2f}° "
          f"| azimuth {solar['azimuth_deg']:.2f}°")
    if render:
        render_current(plan)


PRESETS = (('dawn', '明け方'), ('morning', '朝'), ('noon', '昼'), ('afternoon', '午後'),
           ('sunset', '夕焼け'), ('night', '夜'), ('midnight', '深夜'))


class PRAGUE_OT_light(bpy.types.Operator):
    bl_idname = 'prague.daylight_preview'
    bl_label = '3Dビューの時間帯を切り替え'

    period: bpy.props.EnumProperty(items=[(key, label, '') for key, label in PRESETS])

    def execute(self, context):
        try:
            light(self.period)
        except (ValueError, RuntimeError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class PRAGUE_OT_render(bpy.types.Operator):
    bl_idname = 'prague.render_current'
    bl_label = '画像としてレンダー'

    def execute(self, context):
        try:
            render_current()
        except (ValueError, RuntimeError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class PRAGUE_OT_conversation(bpy.types.Operator):
    bl_idname = 'prague.apply_conversation'
    bl_label = '最新の会話を反映'
    bl_description = '会話から抽出した地名・季節・時間帯の日時で太陽と空を設定'

    def execute(self, context):
        try:
            record = apply_conversation()
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f"{record['place']} / {record['season']} / {record['time_of_day']} / {record['local_datetime']}")
        return {'FINISHED'}


class PRAGUE_PT_light(bpy.types.Panel):
    bl_label = '3Dビューの時間帯'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Prague'

    def draw(self, context):
        for key, label in PRESETS:
            self.layout.operator('prague.daylight_preview', text=label).period = key
        self.layout.separator()
        box = self.layout.box()
        box.label(text='会話の条件')
        box.operator('prague.apply_conversation', icon='IMPORT')
        box.prop(context.scene, 'prague_auto_conversation')
        record = applied_conversation(context.scene)
        if record:
            box.label(text=f"{record['place']} / {record['season']} / {record['time_of_day']}")
            box.label(text=record['local_datetime'])
        self.layout.separator()
        self.layout.operator('prague.render_current', icon='RENDER_STILL')


def register():
    for cls in (PRAGUE_PT_light, PRAGUE_OT_conversation, PRAGUE_OT_render, PRAGUE_OT_light):
        previous = getattr(bpy.types, cls.__name__, None)
        if previous is not None:
            bpy.utils.unregister_class(previous)
    bpy.types.Scene.prague_auto_conversation = bpy.props.BoolProperty(
        name='新しい会話を自動で反映', default=False, update=_toggle_watch,
        description='会話の受信・抽出が終わるたびに、その条件で太陽と空を設定')
    bpy.utils.register_class(PRAGUE_OT_light)
    bpy.utils.register_class(PRAGUE_OT_render)
    bpy.utils.register_class(PRAGUE_OT_conversation)
    bpy.utils.register_class(PRAGUE_PT_light)
    if bpy.context.scene is not None and bpy.context.scene.prague_auto_conversation:
        _start_watch()


if __name__ == '__main__':
    register()
