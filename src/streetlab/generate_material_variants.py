"""Generate or edit object reference images for TRELLIS.2; execution is manual.

Adapted from sao/mvl-skeleton/python/generate_material_variants.py.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


CLASS_PROMPTS = {
    "vase": "a single ceramic vase, simple solid silhouette, matte glaze",
    "desk": "modern wooden study desk, contemporary design",
    "chair": "modern office chair",
    "bookshelf": "modern tall wooden bookshelf filled with books",
    "lamp": "modern desk lamp with a metal shade",
    "plant": "potted plant in a modern ceramic pot",
    "books": "stack of hardcover books",
    "monitor": "modern computer monitor on a stand",
    "rug": "modern woven area rug, viewed at a slight angle",
    "laptop": "modern open laptop computer, contemporary design",
    "mug": "modern ceramic coffee mug",
    "pen_holder": "modern desk pen holder filled with pens and pencils",
    "printer": "modern compact inkjet printer",
    "floor_lamp": "modern floor lamp with a fabric shade",
    "cabinet": "modern small three-drawer storage cabinet, light wood",
    "trash_bin": "modern small waste basket",
    "backpack": "modern school backpack, standing upright",
    "window": "modern double-hung window with white wooden frame, closed glass panes",
    "ceiling_light": "modern flush-mount ceiling light with a frosted glass dome",
}

MATERIAL_ANCHOR = (
    ", physically plausible materials with material-specific surface detail and microtexture, "
    "such as wood grain, woven fibers, brushed metal, ceramic glaze, paper texture, or leaf "
    "veins as appropriate, realistic roughness variation, subtle accurate specular highlights, "
    "natural small imperfections, no uniform plastic appearance"
)

IMAGE_SUFFIX = (
    ", single object, centered, full object visible, isolated on a pure white background, "
    "front three-quarter view, studio product photography lighting, sharp focus, crisp details, "
    "clean silhouette, simple readable shape, no text, no watermark, no extra objects, "
    "no environment, not cropped"
)

# Shared policy: the image supplies the object's identity; no human redesign prompt is needed.
ENHANCE_PROMPT = (
    "Create a clear, high-detail image-to-3D reference of the SAME single dominant object "
    "shown in this crop from a Gaussian-splat scene. Treat the input image as the source "
    "of the object's identity, not as a loose inspiration. Preserve its category, design, "
    "silhouette, proportions, part count, camera viewpoint, orientation, color palette, "
    "visible patterns, age and material cues. Preserve the source world's visual style; "
    "do not turn stylized objects into photorealistic redesigns. Reduce blur, splat haze, "
    "floaters, compression noise and baked cast shadows. Clarify edges and surface details "
    "only where supported by visible evidence. Keep uncertain features simple and conservative; "
    "do not invent ornaments, logos, text or new parts. Do not mirror the visible side to "
    "fabricate hidden detail. This is generative reconstruction, not recovery of ground truth. "
    "Isolate the object, keeping its visible geometry and framing, centered with a small margin. "
    "Remove the surrounding scene, floor and supporting surfaces without deleting parts of "
    "the object. Use neutral diffuse lighting and no cast shadow. No extra objects or watermark."
)


from streetlab.paths import ROOT
JST = timezone(timedelta(hours=9))


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting existing environment values."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def make_run_dir(output_root: Path, timestamp: str) -> Path:
    base = output_root / f"{timestamp}_material_variants"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = output_root / f"{base.name}_{suffix:02d}"
        suffix += 1
    return candidate


def build_plan(args: argparse.Namespace, run_dir: Path) -> list[dict]:
    plan = []
    classes = args.classes or [args.name]
    for asset_class in classes:
        subject = CLASS_PROMPTS[asset_class] if args.classes else (args.prompt or "").strip()
        if args.mode == "enhance":
            prompt = ENHANCE_PROMPT
        elif args.input:
            prompt = (
                "Edit the single target object in the reference image. Requested change: "
                + subject
                + ". Preserve its camera viewpoint, orientation, proportions and overall "
                "structure unless the requested change explicitly changes them. Produce an "
                "isolated object reference suitable for image-to-3D reconstruction. "
                "Show the entire object, centered with a margin. Remove the surrounding scene, "
                "supporting surfaces and cast shadows. No added objects, text or watermark."
                + MATERIAL_ANCHOR + "."
            )
        else:
            prompt = subject + MATERIAL_ANCHOR + IMAGE_SUFFIX
        if args.background == "transparent":
            prompt = prompt.replace("pure white background", "transparent background")
            prompt += " Use a transparent background with no ground plane or cast shadow."
        elif args.input:
            prompt += " Use a pure white background."
        for variant in range(args.start_variant, args.start_variant + args.variants):
            name = f"{asset_class}_v{variant}"
            plan.append(
                {
                    "name": name,
                    "class": asset_class,
                    "variant": variant,
                    "prompt": prompt,
                    "mode": args.mode,
                    "output": str((run_dir / f"{name}.png").resolve()),
                    "status": "planned",
                }
            )
    return plan


def save_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def generate_one(client, args: argparse.Namespace, prompt: str, output: Path) -> dict:
    kwargs = {
        "model": args.model, "prompt": prompt, "size": args.size, "n": 1,
        "quality": args.quality, "output_format": "png",
        "background": "transparent" if args.background == "transparent" else "opaque",
    }
    if args.input:
        with args.input.open("rb") as source:
            response = client.images.edit(image=source, **kwargs)
    else:
        response = client.images.generate(**kwargs)
    if not response.data or not response.data[0].b64_json:
        raise ValueError("The API returned no image data; the request will not be retried.")
    payload = base64.b64decode(response.data[0].b64_json, validate=True)
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The API did not return a PNG; the request will not be retried.")
    # Exclusive creation protects any image already written at this path.
    with output.open("xb") as destination:
        destination.write(payload)
    usage = getattr(response, "usage", None)
    return {
        "request_id": getattr(response, "_request_id", None),
        "usage": usage.model_dump(mode="json") if usage is not None else None,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TRELLIS.2用の小物画像を生成・編集します。SPZの選択や3D化は別の工程です。"
    )
    subject = parser.add_mutually_exclusive_group()
    subject.add_argument(
        "--classes",
        nargs="+",
        choices=sorted(CLASS_PROMPTS),
        help="新規生成する物の種類（指定なしではvaseを1枚）。",
    )
    subject.add_argument("--prompt", help="自由な生成指示、または入力画像への変更指示。")
    parser.add_argument("--input", type=Path, help="小物を写したPNG/JPG/WEBP。指示なしなら同じ物として鮮明化します。")
    parser.add_argument("--name", default=None, help="保存名（指示なしの画像入力ではenhanced_object）。")
    parser.add_argument("--start-variant", type=int, default=1)
    parser.add_argument("--variants", type=int, default=1)
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--quality", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--size", choices=["1024x1024", "1536x1024", "1024x1536", "auto"], default="1024x1024")
    parser.add_argument("--background", choices=["white", "transparent"], default="white")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="APIキーを読む.envファイル。")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs" / "images",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without calling the API.")
    args = parser.parse_args(argv)
    if args.prompt is not None and not args.prompt.strip():
        parser.error("--prompt cannot be empty")
    if args.input and args.classes:
        parser.error("--input cannot be combined with --classes; omit both --classes and --prompt for enhancement")
    args.mode = "enhance" if args.input and not args.prompt else "edit" if args.input else "generate"
    if args.name is None:
        args.name = "enhanced_object" if args.mode == "enhance" else "edited_object"
    if not args.input and not args.classes and not args.prompt:
        args.classes = ["vase"]
    if args.classes:
        args.classes = list(dict.fromkeys(args.classes))
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("--name must use letters A-Z, digits, underscores or hyphens")
    if args.input:
        args.input = args.input.resolve()
        if not args.input.is_file():
            parser.error("--input image file does not exist")
        if args.input.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            parser.error("--input must be PNG/JPG/WEBP, not SPZ or GLB")
        if not 0 < args.input.stat().st_size < 50 * 1024 * 1024:
            parser.error("--input must be nonempty and smaller than 50 MB")
    if args.start_variant < 1:
        parser.error("--start-variant must be at least 1")
    if args.variants < 1:
        parser.error("--variants must be at least 1")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    run_dir = make_run_dir(args.output_root.resolve(), timestamp)
    plan = build_plan(args, run_dir)

    print(f"Output: {run_dir}")
    print(f"Mode: {args.mode} / Model: {args.model}")
    print(f"Plan: {len(plan)} images / Quality: {args.quality} / Background: {args.background}")
    for item in plan:
        print(f"  {item['name']}.png")
        if args.dry_run:
            print(f"    {item['prompt']}")

    if args.dry_run:
        print("Dry run: API was not called and no files were created.")
        return 0

    load_env_file(args.env_file)
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        print(".envにOPENAI_API_KEYを追加してください（WORLD_LABS_API_KEYとは別です）。", file=sys.stderr)
        return 2

    try:
        from openai import OpenAI
    except ImportError:
        print("先に python -m pip install openai を実行してください。", file=sys.stderr)
        return 2
    # Paid requests are never automatically retried after a connection failure.
    client = OpenAI(max_retries=0, timeout=600.0)

    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 2,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "mode": args.mode,
        "input_image": str(args.input) if args.input else None,
        "background": args.background,
        "model": args.model,
        "quality": args.quality,
        "size": args.size,
        "classes": args.classes,
        "start_variant": args.start_variant,
        "variants_per_class": args.variants,
        "material_anchor": MATERIAL_ANCHOR.lstrip(", "),
        "output_dir": str(run_dir),
        "assets": plan,
    }
    manifest_path = run_dir / "manifest.json"
    save_manifest(manifest_path, manifest)

    failures = 0
    for index, item in enumerate(plan, start=1):
        output = Path(item["output"])
        print(f"[{index}/{len(plan)}] Generating {output.name} ...")
        item["status"] = "in_progress"
        save_manifest(manifest_path, manifest)
        try:
            item.update(generate_one(client, args, item["prompt"], output))
            item["status"] = "success"
        except KeyboardInterrupt:
            item["status"] = "outcome_unknown"
            save_manifest(manifest_path, manifest)
            print("中断しました。送信済みの処理は課金される可能性があります。", file=sys.stderr)
            return 130
        except Exception as exc:
            failures += 1
            uncertain = isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in {
                "APIConnectionError", "APITimeoutError"
            }
            item["status"] = "outcome_unknown" if uncertain else "failed"
            item["error_type"] = type(exc).__name__
            item["request_id"] = getattr(exc, "request_id", None)
            item["http_status"] = getattr(exc, "status_code", None)
            # Do not persist or print response bodies, which may contain sensitive inputs.
            print(f"    failed: {type(exc).__name__}. 自動再送しません。", file=sys.stderr)
        save_manifest(manifest_path, manifest)
        if failures:
            print("残りの生成を停止しました。再実行は新しい有料リクエストになります。", file=sys.stderr)
            break
        if index < len(plan) and args.delay:
            time.sleep(args.delay)

    succeeded = sum(item["status"] == "success" for item in plan)
    print(f"Complete: success={succeeded} failed={failures}")
    print(f"Manifest: {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
