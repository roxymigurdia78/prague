"""Preserve historical runs when creating and resuming lighting searches."""
from datetime import datetime
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streetlab.build_world import read, save
from streetlab.lighting_runs import allocate_run, resume_cache, run_name, update_history
from streetlab.lighting_search import run_search


class LightingRunTests(unittest.TestCase):
    def test_model_and_date_names_are_safe_and_never_reuse_a_directory(self):
        when = datetime(2026, 11, 17, 15, 2, 3)
        self.assertEqual(run_name('a_all', when), 'a_all_20261117_150203')
        self.assertNotIn('/', run_name('../model:test', when))
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'runs'
            preview = allocate_run(parent, 'a_all', when=when, preview=True)
            self.assertFalse(parent.exists())
            first = allocate_run(parent, 'a_all', when=when)
            second = allocate_run(parent, 'a_all', when=when)
            self.assertEqual(first, preview)
            self.assertEqual(second.name, first.name + '_02')

    def test_fork_copies_mutable_metadata_and_reuses_images_without_touching_history(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = (Path(directory) / name for name in ('old', 'new'))
            source.mkdir(); destination.mkdir()
            identity = {'model': 'a_all'}
            save(source / 'state.json', {'identity': identity, 'best': 'known-good'})
            for name, content in [('view.png', b'image'), ('settings.json', b'original metadata')]:
                path = source / 'candidates/known-good' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            (source / 'index.html').write_text('historical report')
            save(source / 'round-002.json', [{'id': 'pending'}])
            before = (source / 'state.json').read_bytes()
            resumed = resume_cache(source, destination, identity)
            self.assertEqual(resumed['best'], 'known-good')
            self.assertEqual(read(destination / 'round-002.json'), [{'id': 'pending'}])
            self.assertEqual((destination / 'candidates/known-good/view.png').read_bytes(), b'image')
            (destination / 'candidates/known-good/settings.json').write_text('updated metadata')
            self.assertEqual((source / 'candidates/known-good/settings.json').read_bytes(), b'original metadata')
            self.assertEqual((source / 'state.json').read_bytes(), before)
            self.assertEqual((source / 'index.html').read_text(), 'historical report')
            self.assertFalse((destination / 'index.html').exists())

    def test_new_runs_start_fresh_and_resume_requires_an_explicit_source(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            context = ({'capture': {'white_balance_kelvin': None, 'look': 'None'}, 'views': []},
                       folder, [], [], '昼', 'same-world')
            client = SimpleNamespace(model='a_all', revision='same-weights', url='https://example.invalid')
            with patch('streetlab.lighting_search.capture_context', return_value=context), patch(
                    'streetlab.lighting_search.api_settings', return_value={'LIGHTING_API_URL': client.url,
                                                                           'LIGHTING_MODEL': 'a_all'}), patch(
                    'streetlab.lighting_search.LightingAPI', return_value=client), patch(
                    'streetlab.lighting_search.Search') as search:
                search.return_value.explore.return_value = 'test'
                first = run_search(folder, output=folder / 'runs', rounds=1)
                (first / 'index.html').write_text('old result')
                old=read(first/'state.json')
                old['knobs']['old-best']=old['knobs']['initial']|{'exposure_delta':1}
                old.update(best='old-best',branches=['old-best'])
                save(first/'state.json',old)
                before = (first / 'state.json').read_bytes()
                second = run_search(folder, output=first, rounds=2)
                self.assertNotEqual(first, second)
                self.assertEqual(first.parent, second.parent)
                self.assertIsNone(read(second / 'run.json')['resume_from'])
                self.assertEqual(read(second/'state.json')['best'],'initial')
                self.assertNotIn('old-best',read(second/'state.json')['knobs'])
                self.assertEqual(search.return_value.explore.call_args.kwargs['minutes'],30)
                self.assertEqual((first / 'index.html').read_text(), 'old result')
                self.assertEqual((first / 'state.json').read_bytes(), before)
                self.assertEqual(search.call_count, 2)
                resumed=run_search(folder,resume=first,rounds=2)
                self.assertEqual(read(resumed/'run.json')['resume_from'],str(first))
                self.assertEqual(read(resumed/'state.json')['best'],'old-best')
                client.model = 'different-model'
                fresh=run_search(folder,output=first)
                self.assertEqual(read(fresh/'state.json')['best'],'initial')
                with self.assertRaisesRegex(ValueError, '一致しません'):
                    run_search(folder,resume=first)

    def test_dry_run_does_not_create_a_folder_or_connect_to_the_api(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            context = {'views': []}, folder, [], [], '昼', 'signature'
            with patch('streetlab.lighting_search.capture_context', return_value=context), patch(
                    'streetlab.lighting_search.api_settings', return_value={
                        'LIGHTING_API_URL': 'https://example.invalid', 'LIGHTING_MODEL': 'a_all'}), patch(
                    'streetlab.lighting_search.LightingAPI', side_effect=AssertionError('network used')):
                destination = run_search(folder, minutes=120, dry_run=True)
                stream=io.StringIO()
                with redirect_stdout(stream):run_search(folder,dry_run=True)
                preview=json.loads(stream.getvalue())
                self.assertEqual(preview['minutes'],30)
                self.assertIsNone(preview['rounds'])
                self.assertIsNone(preview['resume_from'])
            self.assertTrue(destination.name.startswith('a_all_'))
            self.assertEqual(list(folder.iterdir()), [])

    def test_history_links_new_and_legacy_results_without_rewriting_them(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            for name in ('a_all_loop_01', 'a_all_20261117_163000'):
                folder = parent / name
                folder.mkdir()
                save(folder / 'summary.json', {'rounds': 19, 'stop_reason': '改善なし'})
                (folder / 'index.html').write_text('unchanged report')
                save(folder / 'state.json', {'identity': {'model': 'a_all'}})
            save(parent / 'a_all_20261117_163000/run.json',
                 {'model': 'a_all', 'created_at': '2026-11-17T16:30:00+09:00'})
            page = update_history(parent).read_text(encoding='utf-8')
            self.assertIn('a_all_loop_01/index.html', page)
            self.assertIn('a_all_20261117_163000/index.html', page)
            self.assertEqual((parent / 'a_all_loop_01/index.html').read_text(), 'unchanged report')


if __name__ == '__main__':
    unittest.main()
