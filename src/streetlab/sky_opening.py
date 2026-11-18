"""Fit coarse semantic sky openings to image boundaries before touching geometry."""
import numpy as np
from PIL import Image
from scipy import ndimage

from streetlab.sky_cleanup import polygon_sky_mask


def refine_opening(rgb, polygons, reference=None):
    import cv2
    native=rgb.shape[:2]
    factor=min(1,768/max(native))
    shape=(max(1,round(native[1]*factor)),max(1,round(native[0]*factor)))
    image=np.asarray(Image.fromarray(rgb).resize(shape,Image.Resampling.BILINEAR)).copy()
    coarse=polygon_sky_mask(image.shape[:2],polygons)
    if not coarse.any():return np.zeros(native,dtype=bool)
    void=(image[:,:,0]>200)&(image[:,:,1]<60)&(image[:,:,2]>200)
    if reference is not None:
        before=np.asarray(Image.fromarray(reference).resize(shape,Image.Resampling.BILINEAR))
        image[void]=before[void]
    else:
        samples=image[coarse&~void]
        image[void]=np.median(samples,axis=0).astype(np.uint8) if len(samples) else (175,175,175)
    distance=ndimage.distance_transform_edt(coarse)
    anchor=coarse&(distance>max(5,distance.max()*.35))
    samples=image[(void&coarse)]
    if len(samples)<20:samples=image[anchor]
    if len(samples)<20:return np.zeros(native,dtype=bool)
    median=np.median(samples,axis=0)
    color_distance=np.linalg.norm(image.astype(float)-median,axis=2)
    local=ndimage.uniform_filter(image.astype(float),size=(5,5,1))
    texture=np.sqrt(np.mean((image-local)**2,axis=2))
    confident=anchor&(color_distance<max(18,np.percentile(np.linalg.norm(samples-median,axis=1),60)))&(texture<10)
    confident|=void&coarse
    margin=max(12,round(min(image.shape[:2])*.2))
    broad=ndimage.distance_transform_edt(~coarse)<=margin
    mask=np.full(image.shape[:2],cv2.GC_PR_BGD,dtype=np.uint8)
    mask[coarse]=cv2.GC_PR_FGD
    mask[~broad]=cv2.GC_BGD
    mask[confident]=cv2.GC_FGD
    if np.count_nonzero(mask==cv2.GC_FGD)<5 or np.count_nonzero(mask==cv2.GC_BGD)<5:
        return np.zeros(native,dtype=bool)
    cv2.setRNGSeed(0)
    cv2.grabCut(image,mask,None,np.zeros((1,65)),np.zeros((1,65)),5,cv2.GC_INIT_WITH_MASK)
    sky=(mask==cv2.GC_FGD)|(mask==cv2.GC_PR_FGD)
    # Retain connected sky regions containing actual positive seeds, not facade
    # interiors that happen to have a similar color.
    labels,_=ndimage.label(sky)
    keep=np.unique(labels[confident]);keep=keep[keep!=0]
    sky=np.isin(labels,keep)
    holes=ndimage.binary_fill_holes(sky)&~sky
    labels,_=ndimage.label(holes)
    sizes=np.bincount(labels.ravel());small=sizes<=sky.size*.003;small[0]=False
    sky|=small[labels]
    result=np.asarray(Image.fromarray(sky).resize((native[1],native[0]),Image.Resampling.NEAREST),dtype=bool)
    # A native-pixel margin protects real roofs, eaves and chimneys.
    return ndimage.binary_erosion(result,iterations=2,border_value=1)


def architecture_evidence(rgb, sky, reference=None, sky_color=None):
    """Explicit facade evidence; absence of a sky label alone is an abstention."""
    void=(rgb[:,:,0]>200)&(rgb[:,:,1]<60)&(rgb[:,:,2]>200)
    if reference is not None and np.count_nonzero(void&sky)>=20:
        samples=reference[void&sky]
    else:samples=rgb[sky&~void]
    if len(samples)<20 and sky_color is None:return np.zeros(sky.shape,dtype=bool)
    color=np.median(samples,axis=0) if len(samples)>=20 else np.asarray(sky_color)
    distance=np.linalg.norm(rgb.astype(float)-color,axis=2)
    threshold=max(35,float(np.percentile(np.linalg.norm(samples.astype(float)-color,axis=1),95))+15) if len(samples)>=20 else 35
    chroma=rgb.max(axis=2).astype(float)-rgb.min(axis=2)
    local=ndimage.uniform_filter(rgb.astype(float),size=(5,5,1))
    texture=np.sqrt(np.mean((rgb-local)**2,axis=2))
    evidence=~sky&~void&(distance>threshold)&((chroma>25)|((texture>12)&(distance>70)))
    return ndimage.binary_erosion(evidence,iterations=2,border_value=0)
