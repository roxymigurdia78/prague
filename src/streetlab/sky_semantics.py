"""Use the existing HPC segmentation transport to exclude sky from building protection."""
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np


def confirmed_sky(rgb,points,semantic):
    """Extend segmentation with tight color matches connected to VLM sky seeds."""
    from scipy import ndimage
    h,w=rgb.shape[:2]
    seeds=[(min(h-1,int(y/1000*h)),min(w-1,int(x/1000*w))) for x,y in points]
    candidate=np.zeros((h,w),dtype=bool)
    chroma=rgb-rgb.mean(axis=-1,keepdims=True)
    for y,x in seeds:
        reference=rgb[y,x]
        reference_chroma=reference-reference.mean()
        candidate|=((np.linalg.norm(rgb-reference,axis=-1)<=.06)
            | ((np.linalg.norm(chroma-reference_chroma,axis=-1)<=.025)
                & (rgb.mean(axis=-1)>=max(.12,float(reference.mean())*.8))))
    labels,_=ndimage.label(candidate)
    ids={int(labels[y,x]) for y,x in seeds}-{0}
    return semantic | np.isin(labels,list(ids))


def ensure(folder):
    from PIL import Image
    folder=Path(folder)
    views=json.loads((folder/'views.json').read_text(encoding='utf-8'))['views']
    destination=folder/'semantic_guard'
    masks=destination/'masks/output'
    if not all((masks/v['id']/'building_mask.png').is_file() for v in views):
        images=destination/'images'
        images.mkdir(parents=True,exist_ok=True)
        for view in views:
            shutil.copy2(folder/(view['id']+'.png'),images/(view['id']+'.png'))
        transport=Path(__file__).parent/'roads/Invoke-Mask2Former.ps1'
        shell=shutil.which('pwsh') or shutil.which('powershell')
        if not shell:
            raise RuntimeError('HPCの空・建物照合にはPowerShellが必要です。')
        print('HPC Mask2Formerで空と建物の保護範囲を照合しています…',flush=True)
        with (destination/'segmentation.log').open('w',encoding='utf-8') as log:
            result=subprocess.run([shell,'-NoProfile','-File',str(transport),'-BatchDirectory',str(images),
                '-OutputDirectory',str(destination/'masks')],stdout=log,stderr=subprocess.STDOUT)
        if result.returncode or not all((masks/v['id']/'sky_mask.png').is_file() for v in views):
            raise RuntimeError('空・建物の照合で停止しました: '+str(destination/'segmentation.log'))
    if all((masks/v['id']/'confirmed_sky_v2.png').is_file() for v in views):
        return
    from streetlab.sky_cleanup import filter_sky_points
    analysis=json.loads((folder/'analysis.json').read_text(encoding='utf-8'))['views']
    pictures=[np.asarray(Image.open(folder/(v['id']+'.png')).convert('RGB'),dtype=np.float32)/255 for v in analysis]
    seeds=filter_sky_points(pictures,analysis)
    for view,rgb in zip(analysis,pictures):
        mask_dir=masks/view['id']
        semantic=np.asarray(Image.open(mask_dir/'sky_mask.png').convert('L'))>127
        confirmed=confirmed_sky(rgb,seeds[view['id']],semantic)
        Image.fromarray(confirmed.astype(np.uint8)*255).save(mask_dir/'confirmed_sky_v2.png')


def allowed(folder, view_id, shape):
    path=Path(folder)/'semantic_guard/masks/output'/view_id/'sky_mask.png'
    confirmed=path.with_name('confirmed_sky_v2.png')
    if confirmed.exists():
        path=confirmed
    if not path.exists():
        return np.ones(shape,dtype=bool)
    try:
        from PIL import Image
    except ModuleNotFoundError:
        import bpy
        picture=bpy.data.images.load(str(path.resolve()),check_existing=False)
        width,height=picture.size
        values=np.empty(height*width*4,dtype=np.float32)
        picture.pixels.foreach_get(values)
        sky=values.reshape(height,width,4)[::-1,:,0]>.5
        bpy.data.images.remove(picture)
    else:
        with Image.open(path) as picture:
            sky=np.asarray(picture.convert('L'))>127
    # Retain the uncertain edge at roofs/chimneys; reject only the interior of sky.
    for _ in range(2):
        p=np.pad(sky,1)
        sky=p[1:-1,1:-1]&p[:-2,1:-1]&p[2:,1:-1]&p[1:-1,:-2]&p[1:-1,2:]
    # Define the uncertain edge at native resolution, then resample identically
    # in Python and Blender. Eroding two pixels after downscaling accepted a
    # wider sky band in review than the geometry worker could actually protect.
    height,width=sky.shape
    y=np.minimum(((np.arange(shape[0])+.5)*height/shape[0]).astype(int),height-1)
    x=np.minimum(((np.arange(shape[1])+.5)*width/shape[1]).astype(int),width-1)
    return ~sky[y[:,None],x[None,:]]
