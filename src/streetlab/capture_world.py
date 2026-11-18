"""Prepare a Marble world and render its surveyed viewpoints with a CAMS sky."""

import argparse
from pathlib import Path
import subprocess

from streetlab.paths import CODE_DIR
from streetlab.sky_cleanup import find_blender


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Marble GLB or prepared street_relight.blend')
    parser.add_argument('--capture-plan', type=Path, required=True, help='Saved road capture_plan.json')
    parser.add_argument('--capture-source', type=Path, help='Blender scene in which the plan was surveyed')
    parser.add_argument('--atmosphere-reference', type=Path, required=True, help='Saved EAC4 JSON')
    parser.add_argument('--settings', type=Path, help='Optional render_views settings JSON')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-up', choices=('marble', 'gltf'), default='marble')
    parser.add_argument('--env-file', type=Path, help='HPC settings for GLB preparation')
    args = parser.parse_args(argv)

    for path in (args.input, args.capture_plan, args.atmosphere_reference, args.capture_source, args.settings):
        if path is not None and not path.is_file():
            parser.error(f'File not found: {path}')
    blender = find_blender()
    source = args.input.resolve()
    if source.suffix.lower() == '.glb':
        from streetlab.prepare_world import main as prepare

        options = [str(source), '--source-up', args.source_up]
        if args.env_file:
            options += ['--env-file', str(args.env_file)]
        source = prepare(options) / 'street_relight.blend'
    elif source.suffix.lower() != '.blend':
        parser.error('Expected a Marble GLB or prepared Blender scene')

    options = ['--capture-plan', str(args.capture_plan.resolve()), '--atmosphere-reference',
               str(args.atmosphere_reference.resolve()), '--output', str(args.output.resolve())]
    for name, path in (('--settings', args.settings), ('--capture-source', args.capture_source)):
        if path:
            options += [name, str(path.resolve())]
    return subprocess.run([str(blender), '--background', '--disable-autoexec', '--python-exit-code', '1',
                           '--python', str(CODE_DIR / 'capture_world_blender.py'), '--',
                           str(source), *options]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
