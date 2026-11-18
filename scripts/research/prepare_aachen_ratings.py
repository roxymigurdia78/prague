"""Prepare blinded Aachen image groups and their initial scoring CSV."""

import argparse
import csv
from io import BytesIO
from itertools import combinations
from pathlib import Path
import random

import numpy as np
from PIL import Image, ImageCms, ImageOps


CONTROLS = ('露出_EV', '色温度相当_K', '色かぶり', 'コントラスト倍率')
MILD = (.35, 450, 4, .12)
STRONG = (1.05, 1350, 12, .34)
FIELDS = ('画像ID', '組番号', '表示順', '元写真番号', '元ファイル', '画像ファイル',
          '種類', *CONTROLS, '点数（1〜5）', 'メモ')
SRGB = ImageCms.createProfile('sRGB')
LUMINANCE = np.array([.2126, .7152, .0722], dtype=np.float32)


def rgb_photo(path):
    with Image.open(path) as photo:
        upright = ImageOps.exif_transpose(photo)
        if upright.info.get('icc_profile'):
            profile = ImageCms.ImageCmsProfile(BytesIO(upright.info['icc_profile']))
            return ImageCms.profileToProfile(upright, profile, SRGB, outputMode='RGB')
        return upright.convert('RGB')


def treatment(index, rng):
    """Spread the controls and directions across sources while keeping 6 images per group."""
    strong = index % 4
    remaining = [(strong + step) % 4 for step in (1, 2, 3)]
    remaining.remove(remaining[(index // 4) % 3])
    pairs = list(combinations(range(4), 2))
    neg = -1 if (index // 4) % 2 else 1
    cases = [('元写真', (0., 0, 0, 1.))]

    def changes(keys, strengths, signs):
        values = [0., 0, 0, 1.]
        for key, strength, sign in zip(keys, strengths, signs):
            value = strength[key] * sign
            if key == 3:
                values[key] = round(1 / (1 - value), 3) if value > 0 else round(1 + value, 3)
            else:
                values[key] = value
        return tuple(values)

    cases.append(('強め単独', changes([strong], [STRONG], [neg])))
    for key in remaining:
        cases.append(('弱め単独', changes([key], [MILD], [neg if (key + index) % 2 else -neg])))
    for pair in (pairs[index % 6], pairs[(index + 3) % 6]):
        signs = rng.choices((-1, 1), k=2)
        cases.append(('組み合わせ', changes(pair, [MILD, MILD], signs)))
    rng.shuffle(cases)
    return cases


def linear_rgb(photo):
    srgb = np.asarray(photo, dtype=np.float32) / 255
    return np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)


def adjust(rgb, settings):
    exposure, temperature, tint, contrast = settings
    # Approximate warmth and tint on an already-developed JPEG in linear sRGB.
    warmth = .31 * temperature / 1000
    magenta = .025 * tint
    gains = np.exp2(np.array([warmth + .3 * magenta, -magenta, -warmth + .3 * magenta], dtype=np.float32))
    gains /= gains @ LUMINANCE
    result = rgb * gains * np.float32(2 ** exposure)
    if contrast != 1:
        luminance = result @ LUMINANCE
        ratio = np.maximum(luminance, 1e-6) / .18
        result *= np.power(ratio, contrast - 1)[..., None]
    result = np.clip(result, 0, 1)
    srgb = np.where(result <= .0031308, result * 12.92, 1.055 * result ** (1 / 2.4) - .055)
    return Image.fromarray(np.rint(srgb * 255).astype(np.uint8))


def generate(source, output, count):
    photos = sorted((p for p in source.iterdir() if p.is_file() and p.stem.isascii()
                     and p.stem.isdecimal() and p.suffix.lower() in ('.jpg', '.jpeg')),
                    key=lambda p: (int(p.stem), p.name))[:count]
    if len(photos) != count or len({int(photo.stem) for photo in photos}) != count:
        raise ValueError('元写真の枚数または番号を確認してください。')
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / '照明採点.csv'
    if csv_path.exists():
        raise FileExistsError(f'採点CSVが既にあります。{csv_path}')
    rows = []
    rng = random.Random(42)
    images = output / 'images'
    images.mkdir(exist_ok=True)
    for index, path in enumerate(photos):
        photo = rgb_photo(path)
        rgb = linear_rgb(photo)
        group = index + 1
        for slot, (kind, controls) in enumerate(treatment(index, rng), 1):
            image_id = f'{group:03d}-{slot}'
            filename = f'images/{image_id}.jpg'
            variant = photo if kind == '元写真' else adjust(rgb, controls)
            variant.save(output / filename, quality=94, subsampling=0, optimize=True)
            rows.append(dict(zip(FIELDS, (image_id, group, slot, int(path.stem), path.name,
                                           filename, kind, *controls, '', ''))))
        if group % 25 == 0 or group == count:
            print(f'{group}/{count} 組を作成', flush=True)
    temporary = csv_path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(csv_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--count', type=int, default=250)
    args = parser.parse_args()
    if args.count < 1:
        parser.error('--count は1以上です。')
    generate(args.source, args.output, args.count)
