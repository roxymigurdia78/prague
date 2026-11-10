import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, Mask2FormerForUniversalSegmentation


MODEL_ID = "facebook/mask2former-swin-large-mapillary-vistas-panoptic"


def detect(image_path, output_dir, processor, model, device):
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    inputs = processor(
        images=image,
        size={"shortest_edge": 640, "longest_edge": 960},
        return_tensors="pt",
    ).to(device)

    with torch.inference_mode():
        labels = processor.post_process_semantic_segmentation(
            model(**inputs), target_sizes=[(image.height, image.width)]
        )[0].cpu().numpy()

    ground_ids = {
        name: int(class_id)
        for class_id, name in model.config.id2label.items()
        if name in {"Road", "Sidewalk", "Pedestrian Area"}
    }
    if len(ground_ids) != 3:
        raise ValueError(f"Unexpected model labels: {ground_ids}")

    selected = np.isin(labels, list(ground_ids.values()))
    mask = Image.fromarray(selected.astype(np.uint8) * 255)
    tinted = Image.blend(image, Image.new("RGB", image.size, "lime"), 0.45)
    mask.save(output_dir / "mask.png")
    sky_ids=[int(class_id) for class_id,name in model.config.id2label.items() if name.lower()=='sky']
    if len(sky_ids)!=1:
        raise ValueError('Expected one Sky class for sky/building protection checks')
    Image.fromarray((labels==sky_ids[0]).astype(np.uint8)*255).save(output_dir/'sky_mask.png')
    building_ids=[int(class_id) for class_id,name in model.config.id2label.items() if name.lower()=='building']
    Image.fromarray(np.isin(labels,building_ids).astype(np.uint8)*255).save(output_dir/'building_mask.png')
    Image.composite(tinted, image, mask).save(output_dir / "overlay.png")
    report = {
        "model": MODEL_ID,
        "device": torch.cuda.get_device_name() if device == "cuda" else "cpu",
        "image_size": list(image.size),
        "total_pixels": int(labels.size),
        "ground_pixels": int(selected.sum()),
        "all_class_pixels": {name:int(np.count_nonzero(labels==int(class_id)))
            for class_id,name in model.config.id2label.items() if np.any(labels==int(class_id))},
        "class_pixels": {
            name: int(np.count_nonzero(labels == class_id))
            for name, class_id in ground_ids.items()
        },
    }
    summary = json.dumps(report, indent=2)
    (output_dir / "result.json").write_text(summary + "\n", encoding="utf-8")
    print(summary)


def main():
    source = Path(sys.argv[1])
    processor = AutoImageProcessor.from_pretrained(MODEL_ID, use_fast=False)
    model = Mask2FormerForUniversalSegmentation.from_pretrained(
        MODEL_ID, use_safetensors=True
    ).eval()
    device = "cpu"
    if torch.cuda.is_available():
        try:
            model = model.to("cuda")
            device = "cuda"
        except Exception as exc:  # includes torch.AcceleratorError
            # The shared GPU may be busy with other training jobs. Fall back to
            # CPU (slower, same model and output) instead of failing the build.
            print(f"GPU unavailable ({exc.__class__.__name__}: {str(exc).splitlines()[0]}); using CPU", file=sys.stderr, flush=True)
            model = model.to("cpu")
            torch.cuda.empty_cache()
    print(f"Mask2Former device: {device}", file=sys.stderr, flush=True)

    def run(image_path, output_dir):
        nonlocal model, device
        try:
            detect(image_path, output_dir, processor, model, device)
        except Exception as exc:  # includes torch.AcceleratorError
            if device != "cuda" or "out of memory" not in str(exc).lower():
                raise
            print("GPU ran out of memory during inference; retrying on CPU", file=sys.stderr, flush=True)
            model = model.to("cpu")
            device = "cpu"
            torch.cuda.empty_cache()
            detect(image_path, output_dir, processor, model, device)

    if source.is_dir():
        for image_path in sorted(source.glob("*.png")):
            run(image_path, source.parent / "output" / image_path.stem)
    else:
        run(source, source.parent)


if __name__ == "__main__":
    main()
