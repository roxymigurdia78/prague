"""Inspect actual scrap pixels with their surroundings before declaring sky clear."""
from hashlib import sha256
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from streetlab.cleanup_convergence import read, save
from streetlab.sky_cleanup import hpc_settings, polygon_sky_mask
from streetlab.sky_loss_confirmation import confirm


def review_fragments(folder, metrics, workspace, model, env_file, *, pass_index=0, client=None):
    from streetlab.sky_residual_loop import empty, floating_residual_mask, thin_residual_mask

    owned=False
    if not metrics.get('mask_files'):
        raise ValueError('Fragment inspection requires masks for the actual camera images')
    folder,workspace=Path(folder),Path(workspace)
    workspace.mkdir(parents=True,exist_ok=True)
    result=metrics.copy()
    totals={'thin_pixels':0,'fragment_pixels':0,'proposal_pixels':0,'supporting_views':0,
            'uncertain_fragment_pixels':0,'retained_structure_pixels':0}
    artifacts={};decisions=[]
    pad=(48,96,160)[pass_index%3]
    try:
        for view,mask_file in metrics.get('mask_files',{}).items():
            with Image.open(folder/(view+'_after.png')) as image:rgb=np.asarray(image.convert('RGB'))
            with Image.open(mask_file) as image:positive=np.asarray(image.convert('L'))>0
            if positive.shape!=rgb.shape[:2]:raise ValueError('Fragment review mask dimensions changed')
            thin=thin_residual_mask(rgb);fragments=floating_residual_mask(rgb)
            scraps=thin|fragments
            labels,count=ndimage.label(scraps,np.ones((3,3)))
            regions,_=ndimage.label((positive&~empty(rgb))&~scraps,np.ones((3,3)))
            # Inspect coarse foreground proposals as well, but keep tiny scraps
            # separate so a neighboring facade cannot determine their verdict.
            labels[regions>0]=regions[regions>0]+count
            artifact=np.zeros(rgb.shape[:2],dtype=bool)
            building=artifact.copy();uncertain=artifact.copy()
            for number,slices in enumerate(ndimage.find_objects(labels),1):
                if slices is None:continue
                y0=max(0,slices[0].start-pad);y1=min(rgb.shape[0],slices[0].stop+pad)
                x0=max(0,slices[1].start-pad);x1=min(rgb.shape[1],slices[1].stop+pad)
                local=labels[y0:y1,x0:x1]==number
                before=rgb[y0:y1,x0:x1].copy()
                key=sha256((view+str([x0,y0,x1,y1])).encode()+local.tobytes()+before.tobytes()).hexdigest()[:16]
                entry=workspace/'components'/key;entry.mkdir(parents=True,exist_ok=True)
                after=before.copy();after[local]=[255,0,255]
                Image.fromarray(before).save(entry/'before.png');Image.fromarray(after).save(entry/'preview.png')
                if client is None:
                    from openai import OpenAI
                    token,endpoint,_=hpc_settings(env_file,model=model)
                    client=OpenAI(api_key=token,base_url=endpoint,timeout=180,max_retries=2,
                                  default_headers={'User-Agent':'curl/8.5.0'})
                    owned=True
                verdict=confirm(client,model,entry/'before.png',entry/'preview.png',local,entry,
                                context=True,context_pad=pad)
                target={'sky':artifact,'building':building,'uncertain':uncertain}[verdict['verdict']]
                target[y0:y1,x0:x1]|=local
                decisions.append({'view':view,'pixels':int(local.sum()),'box':[x0,y0,x1,y1],**verdict})
            negative_file=metrics.get('negative_mask_files',{}).get(view)
            if negative_file:
                with Image.open(negative_file) as image:negative=np.asarray(image.convert('L'))>0
            else:negative=np.zeros(positive.shape,dtype=bool)
            if positive.shape!=rgb.shape[:2] or negative.shape!=positive.shape:
                raise ValueError('Fragment review masks do not match camera pixels')
            positive |= artifact | (ndimage.binary_dilation(artifact,iterations=2)&empty(rgb))
            positive &= ~(building|uncertain)
            negative[artifact]=False;negative |= building|uncertain
            Image.fromarray(positive.astype('uint8')*255).save(mask_file)
            if negative_file:Image.fromarray(negative.astype('uint8')*255).save(negative_file)
            artifact_file=workspace/(view+'_verified_artifacts.png')
            Image.fromarray(artifact.astype('uint8')*255).save(artifact_file)
            artifacts[view]=str(artifact_file.resolve())
            totals['thin_pixels']+=int((thin&~building).sum())
            totals['fragment_pixels']+=int((fragments&~building).sum())
            totals['proposal_pixels']+=int((positive&~empty(rgb)).sum())
            totals['supporting_views']+=bool(positive.any())
            totals['uncertain_fragment_pixels']+=int(uncertain.sum())
            totals['retained_structure_pixels']+=int(building.sum())
    finally:
        if owned:client.close()
    result.update(totals,artifact_masks=artifacts,fragment_review_complete=True)
    save(workspace/'fragment_review.json',{'decisions':decisions,'metrics':totals})
    return result


def refine_protection(folder, protection, metrics, destination):
    """Release only an old region whose every surviving pixel is verified scrap."""
    if not protection:return protection
    views=0
    for path in metrics.get('artifact_masks',{}).values():
        with Image.open(path) as image:views+=bool(np.asarray(image.convert('L')).any())
    if views<2:
        return protection
    rows=[];released=[]
    from streetlab.sky_residual_loop import empty
    for row in read(Path(protection)/'sky_protection_review.json')['views']:
        name=row['id'];artifact_file=metrics.get('artifact_masks',{}).get(name)
        if not artifact_file:
            rows.append(row);continue
        with Image.open(Path(folder)/(name+'_after.png')) as image:rgb=np.asarray(image.convert('RGB'))
        with Image.open(artifact_file) as image:verified=np.asarray(image.convert('L'))>0
        kept=[]
        for polygon in row['protected_polygons']:
            visible=polygon_sky_mask(rgb.shape[:2],[polygon])&~empty(rgb)
            if visible.any() and np.all(verified[visible]):released.append({'view':name,'polygon':polygon})
            else:kept.append(polygon)
        rows.append({**row,'protected_polygons':kept})
    if not released:return protection
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    save(destination/'sky_protection_review.json',{'views':rows})
    save(destination/'released_artifacts.json',{'previous':str(protection),'verified_artifacts_only':released})
    return str(destination)
