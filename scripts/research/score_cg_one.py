"""Score one CG image with the same Qwen before and after the Aachen LoRA."""

import argparse
import json
import math
import os
from pathlib import Path


PROMPT = (
    "この画像は、夏の昼、雲のない空のヨーロッパの街並みです。"
    "その条件で、空・建物・路面の明るさ、色、影がどれくらい自然に見えるかを"
    "1〜5点で評価してください。5点は光にほとんど違和感がない、"
    "3点は全体としてありそうだが一部に違和感がある、1点は光の違和感が大きい、です。"
    "2点と4点はその間です。建物の形の粗さや構図の好みは採点から外してください。"
    "\n回答は、半角数字の1、2、3、4、5のいずれか1文字だけにしてください。"
    "説明、前置き、「点」、句読点は付けないでください。"
)
SCORES = range(1, 6)


def score(model, inputs, token_ids):
    import torch

    with torch.inference_mode():
        logits = model(**inputs, use_cache=False, logits_to_keep=1).logits[0, -1].float()
        if not torch.isfinite(logits).all().item():
            raise RuntimeError("Qwenの予測に無効な数値が含まれています。")
        probabilities = torch.softmax(logits[token_ids], dim=-1).cpu().tolist()
        numeric_mass = torch.softmax(logits, dim=-1)[token_ids].sum().item()
    return sum(value * p for value, p in zip(SCORES, probabilities)), numeric_mass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--human-score", type=int, choices=SCORES, required=True)
    args = parser.parse_args()

    model_dir = Path.home() / "light_eval/models/qwen3.8-27b"
    adapter = args.adapter.resolve()
    for required in (args.image, model_dir / "config.json", adapter / "adapter_config.json",
                     adapter / "adapter_model.safetensors"):
        if not required.is_file():
            parser.error(f"ファイルが見つかりません: {required}")
    config = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    if Path(config.get("base_model_name_or_path") or "").name != model_dir.name:
        parser.error(f"LoRAの学習元とノートのQwenが一致しません: {config.get('base_model_name_or_path')}")

    # Match the loader and numeric score calculation in the Aachen notebook.
    os.environ["HF_DEACTIVATE_ASYNC_LOAD"] = "1"
    import torch
    from PIL import Image, ImageOps
    from peft import PeftModel
    from transformers import AutoModelForImageTextToText, AutoProcessor, modeling_utils

    if not torch.cuda.is_available():
        parser.error("このSSHセッションからCUDA GPUが見えていません。")
    total_bytes = torch.cuda.get_device_properties(0).total_memory
    torch.cuda.memory.set_per_process_memory_fraction(min((64 * 1024**3) / total_bytes, 1.0), device=0)
    processor = AutoProcessor.from_pretrained(str(model_dir), local_files_only=True)
    tokens = [processor.tokenizer.encode(str(value), add_special_tokens=False) for value in SCORES]
    if any(len(ids) != 1 for ids in tokens) or len({ids[0] for ids in tokens}) != 5:
        raise RuntimeError("点数1〜5のトークンを一意に取得できません。")
    token_ids = [ids[0] for ids in tokens]
    messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": PROMPT}]}]
    prompt = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    with Image.open(args.image) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    inputs = processor(text=[prompt], images=[image], return_tensors="pt")

    original_safe_open = modeling_utils.safe_open

    def safe_open_with_pread(*open_args, **kwargs):
        return original_safe_open(*open_args, **(kwargs | {"backend": "pread"}))

    print("Qwen本体を読み込んでいます。数分かかる場合があります。", flush=True)
    modeling_utils.safe_open = safe_open_with_pread
    try:
        model = AutoModelForImageTextToText.from_pretrained(
            str(model_dir), dtype=torch.bfloat16, device_map="cuda", local_files_only=True
        )
    finally:
        modeling_utils.safe_open = original_safe_open
    model.eval().requires_grad_(False)
    inputs = inputs.to("cuda:0")

    before, before_mass = score(model, inputs, token_ids)
    print(f"学習前: {before:.3f}点。数字1〜5の確率合計: {before_mass:.1%}", flush=True)
    model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False, local_files_only=True)
    model.eval()
    after, after_mass = score(model, inputs, token_ids)
    print(f"学習後: {after:.3f}点。数字1〜5の確率合計: {after_mass:.1%}")
    print(f"あなた: {args.human_score}点")
    print(f"あなたとの点差: 学習前 {math.fabs(before - args.human_score):.3f}"
          f" → 学習後 {math.fabs(after - args.human_score):.3f}")


if __name__ == "__main__":
    main()
