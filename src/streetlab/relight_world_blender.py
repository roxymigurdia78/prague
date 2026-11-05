"""Replace unlit surfaces with textured Principled BSDF; save a camera-free dark scene."""
import json
from pathlib import Path

import bpy


def upstream(start):
    found=set()
    def visit(node):
        if node in found:return
        found.add(node)
        for socket in node.inputs:
            for link in socket.links:visit(link.from_node)
    visit(start)
    return found


def convert_material(material):
    material.use_nodes=True
    nodes=material.node_tree.nodes
    outputs=[n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    if not outputs:raise ValueError('Material has no active surface output: '+material.name)
    reachable=upstream(outputs[0])
    shaders=[n for n in reachable if n.type in ('EMISSION','BSDF_PRINCIPLED')]
    if not shaders:raise ValueError('Unsupported surface material: '+material.name)
    # Imported unlit materials wrap an Emission shader in a light-path mix.
    shader=next((n for n in shaders if n.type=='EMISSION'),shaders[0])
    color=shader.inputs['Color' if shader.type=='EMISSION' else 'Base Color']
    source=color.links[0].from_socket if color.is_linked else None
    default=tuple(color.default_value)
    retained=upstream(source.node) if source else set()
    source_node=source.node.name if source else None
    for node in list(nodes):
        if node not in retained:nodes.remove(node)
    surface=nodes.new('ShaderNodeBsdfPrincipled')
    surface.label='照明で明るさが変わる素材'
    surface.inputs['Base Color'].default_value=default
    surface.inputs['Metallic'].default_value=0
    surface.inputs['Emission Color'].default_value=(0,0,0,1)
    surface.inputs['Emission Strength'].default_value=0
    output=nodes.new('ShaderNodeOutputMaterial')
    output.is_active_output=True
    surface.location=(100,0);output.location=(420,0)
    if source:material.node_tree.links.new(source,surface.inputs['Base Color'])
    material.node_tree.links.new(surface.outputs['BSDF'],output.inputs['Surface'])
    material['relight_prepared']=True
    return {'material':material.name,'color_source':source_node,'emission_strength':0}


def save_relight_scene(folder):
    folder=Path(folder)
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
    if not meshes:raise ValueError('No street geometry')
    materials={slot.material for obj in meshes for slot in obj.material_slots if slot.material}
    if any(not obj.material_slots for obj in meshes):raise ValueError('Street mesh has no material')
    converted=[convert_material(m) for m in sorted(materials,key=lambda m:m.name)]
    for obj in list(bpy.data.objects):
        if obj.type in ('CAMERA','LIGHT'):bpy.data.objects.remove(obj,do_unlink=True)
    for scene in bpy.data.scenes:
        scene.camera=None
        world=bpy.data.worlds.new('Lighting starts at zero')
        world.use_nodes=True
        nodes=world.node_tree.nodes;nodes.clear()
        background=nodes.new('ShaderNodeBackground')
        background.inputs['Color'].default_value=(0,0,0,1)
        background.inputs['Strength'].default_value=0
        output=nodes.new('ShaderNodeOutputWorld')
        world.node_tree.links.new(background.outputs[0],output.inputs['Surface'])
        scene.world=world
        scene.render.film_transparent=False
        scene.render.use_persistent_data=False
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                space=area.spaces.active
                space.shading.type='RENDERED'
                space.shading.use_scene_lights=True
                space.shading.use_scene_world=True
                space.region_3d.view_perspective='PERSP'
    bpy.data.orphans_purge(do_recursive=True)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:obj.select_set(True)
    glb=folder/'street_relight.glb'
    bpy.ops.export_scene.gltf(filepath=str(glb),export_format='GLB',use_selection=True,export_animations=False)
    target=folder/'street_relight.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(target),compress=True)
    bpy.ops.wm.open_mainfile(filepath=str(target))
    assert not bpy.data.cameras and not bpy.data.lights
    for material in bpy.data.materials:
        if material.users:
            output=next(n for n in material.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output)
            shader=output.inputs['Surface'].links[0].from_node
            assert shader.type=='BSDF_PRINCIPLED' and shader.inputs['Emission Strength'].default_value==0
    report={'materials':converted,'world_strength':0,'cameras':0,'lights':0,
            'faces':sum(len(o.data.polygons) for o in bpy.data.objects if o.type=='MESH'),
            'blend':str(target),'glb':str(glb),
            'note':'Baked shadows and light colors in the original texture are retained; this is not inverse rendering.'}
    (folder/'relight_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
