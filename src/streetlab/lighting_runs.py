"""Allocate separate, traceable lighting runs and reuse immutable image caches."""
from datetime import datetime
from html import escape
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from urllib.parse import quote
from zoneinfo import ZoneInfo

from streetlab.build_world import read, run_lock, save


def run_name(model, when=None):
    when = when or datetime.now(ZoneInfo('Asia/Tokyo'))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', model).strip(' ._')[:32] or 'model'
    return f'{name}_{when:%Y%m%d_%H%M%S}'


def allocate_run(parent, model, *, preview=False, when=None):
    parent = Path(parent)
    base = run_name(model, when)
    for number in range(1, 10000):
        path = parent / (base if number == 1 else f'{base}_{number:02d}')
        if preview:
            if not path.exists():
                return path
        else:
            try:
                path.mkdir(parents=True, exist_ok=False)
                return path
            except FileExistsError:
                continue
    raise RuntimeError('実行フォルダを確保できません。別の保存先を指定してください。')


def resume_cache(source, destination, identity):
    """Fork a consistent state; never edit or relocate the source run."""
    source, destination = Path(source), Path(destination)
    with run_lock(source):
        state = read(source / 'state.json')
        if state['identity'] != identity:
            raise ValueError('再開元の街・撮影条件・審査モデル・乱数と一致しません。')
        for name in ('candidates', 'comparisons', 'panels'):
            origin = source / name
            if origin.is_dir():
                shutil.copytree(origin, destination / name, copy_function=copy_cached_file)
        for path in source.glob('round-*.json'):
            shutil.copy2(path, destination / path.name)
        # Absolute paths in summary.json belong to the historical run. A new
        # summary and final .blend files are generated in the new destination.
        save(destination / 'state.json', state)
    return state


def copy_cached_file(source, destination):
    # Existing camera images and panels are never overwritten by the search.
    # Metadata, requests and logs can change, so those always get separate copies.
    if Path(source).suffix.lower() in ('.png', '.jpg', '.jpeg'):
        try:
            os.link(source, destination)
            return destination
        except OSError:
            pass  # Different drives/filesystems can use a normal copy instead.
    return shutil.copy2(source, destination)


def update_history(parent):
    """Link completed and pending reports without modifying historical folders."""
    parent = Path(parent)
    entries = []
    for folder in parent.iterdir():
        if not folder.is_dir():
            continue
        try:
            metadata = read(folder / 'run.json') if (folder / 'run.json').is_file() else {}
            summary_file = folder / 'summary.json'
            summary = read(summary_file) if summary_file.is_file() else None
            if not metadata and summary is None:
                continue
            if not metadata:
                state = read(folder / 'state.json')
                metadata = {'model': state['identity']['model'], 'created_at': datetime.fromtimestamp(
                    summary_file.stat().st_mtime, ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds')}
            if not all(isinstance(metadata.get(key), str) for key in ('model', 'created_at')):
                continue
            ready = summary is not None and (folder / 'index.html').is_file()
            label = f'{summary["rounds"]}周 · {summary["stop_reason"]}' if ready else '結果未保存'
            target = f'{quote(folder.name)}/index.html'
            title = f'<a href="{target}">{escape(folder.name)}</a>' if ready else escape(folder.name)
            entries.append((metadata['created_at'], f'<tr><td>{escape(metadata["created_at"])}</td>'
                            f'<td>{escape(metadata["model"])}</td><td>{title}</td><td>{escape(label)}</td></tr>'))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # An unrelated or incomplete folder does not hide other runs.
    entries.sort(key=lambda entry: entry[0], reverse=True)
    html = ('<!doctype html><html lang="ja"><meta charset="utf-8"><title>照明探索の履歴</title>'
            '<style>body{font:16px system-ui;margin:32px}td,th{padding:10px;text-align:left}'
            'table{border-collapse:collapse}tr{border-bottom:1px solid #ddd}</style>'
            '<h1>照明探索の履歴</h1><p>日時は日本時間です。従来のフォルダは最終保存日時を表示します。</p>'
            '<table><tr><th>日時</th><th>モデル</th><th>保存先・結果</th><th>状態</th></tr>'
            + ''.join(row for _, row in entries) + '</table></html>')
    with TemporaryDirectory(dir=parent, prefix='.lighting-history-') as stage:
        pending = Path(stage) / 'index.html'
        pending.write_text(html, encoding='utf-8')
        pending.replace(parent / 'lighting_history.html')
    return parent / 'lighting_history.html'
