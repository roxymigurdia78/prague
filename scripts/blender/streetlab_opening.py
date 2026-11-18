bl_info = {'name': 'Streetlab opening view', 'author': 'Streetlab',
           'version': (1, 0, 0), 'blender': (4, 0, 0), 'category': '3D View'}

import bpy
from bpy.app.handlers import persistent


def apply_opening_view():
    scene = bpy.context.scene
    if not scene or not scene.get('streetlab_open_rendered', False):
        return None
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.shading.type = 'RENDERED'
    return None


@persistent
def on_load(_unused):
    if not bpy.app.background and not bpy.app.timers.is_registered(apply_opening_view):
        bpy.app.timers.register(apply_opening_view, first_interval=0.5)


def register():
    if on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load)
    on_load(None)


def unregister():
    if on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load)
    if bpy.app.timers.is_registered(apply_opening_view):
        bpy.app.timers.unregister(apply_opening_view)
