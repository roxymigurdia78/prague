from hashlib import sha256
import tempfile
import random
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import numpy as np

from streetlab.build_world import read, save
from streetlab.lighting_placement import load_anchors, load_legacy_anchors, selected_anchors
from streetlab.lighting_search import Search, SearchBudgetExpired, baseline_knobs, parse_judgment, proposals
from streetlab.lighting_optimizer import AdaptiveSampler


def new_state():
    capture = {'white_balance_kelvin': None, 'look': 'None'}
    return {'knobs': {'initial': baseline_knobs(capture)}, 'history': [],
            'branches': ['initial'], 'best': 'initial', 'stall': 0}


class LightingSearchTests(unittest.TestCase):
    def test_final_exports_use_the_run_name_and_keep_original_lighting_for_the_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            info = {'name': 'a_all_20261117_163000'}
            context = {'views': []}, output, [], [], '昼', 'signature'

            def save_blend(request, folder):
                self.assertTrue(request['original_lighting'])
                (folder / request['blend_filename']).touch()
                save(folder / 'settings.json', {})

            with patch('streetlab.lighting_search.render_candidate', side_effect=save_blend) as render:
                search = Search(output, context, None, info)
                result = search.export('initial', {})
                self.assertEqual(result.name, 'a_all_20261117_163000_initial.blend')
                search.export('initial', {})
                self.assertEqual(render.call_count, 1)

    def test_timed_search_reopens_stalled_branches_and_preserves_the_incumbent(self):
        with tempfile.TemporaryDirectory() as directory:
            context = {'views': []}, Path(directory), [], [], '昼', 'signature'
            search = Search(Path(directory), context, None)
            state = new_state()
            state['stall'] = 4
            clock = [0.]

            def complete_round(current, seed):
                self.assertTrue(current.pop('restart_pending'))
                self.assertEqual(current['best'], 'initial')
                current['history'].append({'round': len(current['history']) + 1})
                current['stall'] = 4
                clock[0] += 30

            with patch('streetlab.lighting_search.time.monotonic', side_effect=lambda: clock[0]), patch.object(
                    search, 'run_round', side_effect=complete_round):
                reason = search.explore(state, 42, minutes=1)
            self.assertIn('時間上限', reason)
            self.assertEqual(len(state['history']), 2)
            self.assertEqual(state['restarts'], 2)
            self.assertIsNone(search.deadline)
            with patch.object(search, 'run_round') as run:
                self.assertEqual(search.explore(state, 42, rounds=20), '改善なし')
                run.assert_not_called()

    def test_expired_budget_does_not_start_a_new_render(self):
        with tempfile.TemporaryDirectory() as directory:
            context = {'views': [{'image': 'view.png'}]}, Path(directory), [], [], '昼', 'signature'
            search = Search(Path(directory), context, None)
            search.deadline = 10
            with patch('streetlab.lighting_search.time.monotonic', return_value=10), patch(
                    'streetlab.lighting_search.render_candidate') as render:
                with self.assertRaises(SearchBudgetExpired):
                    search.images('next', {}, ['view.png'])
                render.assert_not_called()

    def test_legacy_plan_keeps_the_reviewed_light_power_on_the_same_street(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / 'street.blend'
            source.write_bytes(b'known street')
            save(folder / 'job.json', {'sha256': sha256(source.read_bytes()).hexdigest()})
            save(folder / 'result.json', {'last_plan': str(folder / 'round_01' / 'plan.json')})
            (folder / 'round_01').mkdir()
            light = {'id': 'L000', 'kind': 'streetlight', 'position': [1, 2, 3],
                     'normal': [0, 1, 0], 'size': .2, 'enabled': True, 'energy_factor': .3}
            save(folder / 'round_01' / 'plan.json', {'unit': .7, 'lights': [light,
                light | {'id': 'L001', 'enabled': False}]})
            anchors, _, _ = load_legacy_anchors(folder, source)
            self.assertEqual([(a['id'], a['energy_factor']) for a in anchors], [('L000', .3)])
            state = new_state()
            base = baseline_knobs({'white_balance_kelvin': None, 'look': 'None'}, anchors)
            state['knobs']['initial'] = base
            state['legacy_placement'] = True
            self.assertEqual(proposals(state, 0, 42)[0]['knobs'], base | {'fixture_fraction': 1.})
            source.write_bytes(b'another street')
            with self.assertRaises(ValueError):
                load_legacy_anchors(folder, source)

    def test_inventory_anchors_feed_night_search_and_keep_selection_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inventory.json'
            shape = {'position': [0, 1, 2], 'normal': [0, 1, 0], 'width': .3, 'height': .5}
            lamps = [shape | {'id': f'FIXTURE_{i}', 'status': 'supported', 'kind': 'streetlight'}
                     for i in range(3)]
            window = shape | {'id': 'WINDOW_001', 'kind': 'window', 'status': 'supported'}
            doubtful = lamps[-1] | {'id': 'FIXTURE_review', 'status': 'review'}
            save(path, {'objects': [*lamps, window, doubtful], 'unit': .7})
            anchors, unit = load_anchors(path)
            self.assertEqual(unit, .7)
            self.assertEqual(len(anchors), 4)
            save(path, {'objects': [doubtful, window, *reversed(lamps)], 'unit': .7})
            self.assertEqual(load_anchors(path)[0], anchors)
        state = new_state()
        base = baseline_knobs({'white_balance_kelvin': None, 'look': 'None'}, anchors)
        self.assertFalse(selected_anchors(anchors, base))
        partial = base | {'fixture_fraction': .35, 'window_fraction': 1.}
        first = selected_anchors(anchors, partial)
        other = selected_anchors(anchors, partial | {'fixture_selection': .5})
        self.assertNotEqual(first[0]['id'], other[0]['id'])
        self.assertEqual(first[1], other[1])
        state['knobs']['initial'] = base
        state['night'] = True
        trials = proposals(state, 0, 42)
        self.assertEqual(len(trials), 24)
        self.assertTrue(all(row['knobs']['sun_multiplier'] == 1 for row in trials))
        self.assertTrue(any(row['knobs']['fixture_fraction'] == 0 for row in trials))
        self.assertTrue(any(row['knobs']['window_fraction'] > 0 for row in trials))

    def test_next_round_grows_branches_combines_parents_and_searches_more_widely(self):
        state = new_state()
        first = proposals(state, 0, 42)
        self.assertEqual(len(first), 24)
        self.assertEqual(proposals(state, 0, 42), first)
        state['branches'] = [row['id'] for row in first[:6]]
        state['best'] = state['branches'][0]
        state['knobs'].update({row['id']: row['knobs'] for row in first})
        second = proposals(state, 1, 42)
        self.assertEqual(len(second), 12)
        self.assertTrue(all(row['id'] not in state['knobs'] for row in second))
        self.assertEqual({row['kind'] for row in second}, {'variation', 'merge', 'broad'})
        state['stall'] = 3
        self.assertTrue(all(row['kind'] == 'broad' for row in proposals(state, 4, 42)))

    def test_confirmed_wins_teach_coupled_moves_and_failures_change_the_step_size(self):
        state = new_state()
        ranges = {'world_multiplier': (.5, 2, 2), 'exposure_delta': (-2, 2, 2)}
        rows = []
        for index in range(12):
            knobs = state['knobs']['initial'] | {'world_multiplier': 1.2, 'exposure_delta': -.4}
            key = f'win-{index}'
            state['knobs'][key] = knobs
            rows.append({'id': key, 'knobs': knobs, 'parents': ['initial'],
                         'confirmed_margins': {'initial': 2}})
        state['history'] = [{'trials': rows}]
        learned = AdaptiveSampler(state, 'initial', ranges, ('None',))
        self.assertLess(learned.diagnostics['covariance'][0][1], -.5)
        rng = random.Random(21)
        moves = np.array([[sample['world_multiplier'], sample['exposure_delta']]
                          for sample in (learned.sample(rng) for _ in range(600))])
        self.assertLess(np.corrcoef(moves.T)[0, 1], -.3)
        self.assertTrue(np.all((moves[:, 0] >= .5) & (moves[:, 0] <= 2)))
        self.assertTrue(np.all((moves[:, 1] >= -2) & (moves[:, 1] <= 2)))
        # Screening results must not override the full-view learning signal.
        before = learned.diagnostics
        rows.append({'id': 'screen-only', 'knobs': state['knobs']['initial'],
                     'parents': ['initial'], 'screen_margin': 4})
        self.assertEqual(AdaptiveSampler(state, 'initial', ranges, ('None',)).diagnostics, before)
        for index in range(5):
            rows.append(rows[0] | {'id': f'loss-{index}', 'confirmed_margins': {'initial': -2}})
        finer = AdaptiveSampler(state, 'initial', ranges, ('None',))
        self.assertLess(finer.scale, learned.scale)
        rows.extend([rows[-1]] * 12)
        reopened = AdaptiveSampler(state, 'initial', ranges, ('None',))
        self.assertGreater(reopened.scale, finer.scale)

    def test_one_branch_gets_fine_candidates_and_can_use_decimal_precision(self):
        state = new_state()
        rows = proposals(state, 1, 42)
        self.assertEqual(sum(row['kind'] == 'variation' for row in rows), 10)
        self.assertEqual(sum(row['kind'] == 'broad' for row in rows), 2)
        self.assertTrue(any(round(row['knobs']['sun_multiplier'], 2) != row['knobs']['sun_multiplier']
                            for row in rows))
        self.assertEqual(rows, proposals(state, 1, 42))

    def test_resumes_partial_round_reuses_images_and_votes_and_compares_heldout(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            baseline = output / 'baseline'
            baseline.mkdir()
            spec = {'blend': str(baseline / 'initial.blend'),
                    'views': [{'image': f'view_{i}.png'} for i in range(6)]}
            names = [view['image'] for view in spec['views']]
            for name in names:
                Image.new('RGB', (32, 16), 'black').save(baseline / name)
            context = spec, baseline, [names[:2], names[2:4]], names[4:], '昼', 'signature'
            state = new_state()
            high = state['knobs']['initial'] | {'exposure_delta': 1}
            low = state['knobs']['initial'] | {'exposure_delta': -1}
            first = [{'id': 'high', 'knobs': high, 'parents': ['initial'], 'kind': 'broad'},
                     {'id': 'low', 'knobs': low, 'parents': ['initial'], 'kind': 'broad'}]
            render_calls = []
            judge_calls = []

            def render(request, folder):
                render_calls.append((folder, request['save_blend'], [v['image'] for v in request['views']]))
                if request['save_blend']:
                    (folder / request.get('blend_filename', 'scene.blend')).touch()
                    save(folder / 'settings.json', {})
                else:
                    for view in request['views']:
                        Image.new('RGB', (32, 16), 'white').save(folder / view['image'])

            def judge(left, right, condition):
                judge_calls.append((left.name, right.name))
                ranks = {'high': 1, 'initial': 0, 'low': -1}
                a, b = (ranks[path.name.split('-')[0]] for path in (left, right))
                return {'winner': 'A' if a > b else 'B', 'reason': condition}

            def interrupt(left, right, condition):
                if judge_calls:
                    raise RuntimeError('interrupted')
                return judge(left, right, condition)

            with patch('streetlab.lighting_search.render_candidate', side_effect=render), patch(
                    'streetlab.lighting_search.proposals',
                    side_effect=lambda *_: [row.copy() for row in first]) as sampler:
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    Search(output, context, interrupt).run_round(state, 42)
                previous_calls = judge_calls.copy()
                self.assertEqual(len(render_calls), 1)
                # Resuming a pending round must also work after changing samplers.
                sampler.side_effect = AssertionError('pending proposals were regenerated')
                search = Search(output, context, judge)
                search.run_round(state, 42)
                self.assertEqual(judge_calls.count(previous_calls[0]), 1)
                self.assertEqual(read(output / 'state.json')['best'], 'high')
                self.assertEqual(sum(call[0].name == 'high' and call[2] == [names[0], names[2]]
                                     for call in render_calls), 1)
                self.assertFalse(any(name in context[3] for _, _, shots in render_calls for name in shots))
                search.finish(state, 'テスト終了')
                summary = read(output / 'summary.json')
                self.assertEqual(summary['recommended'], 'high')
                self.assertEqual(summary['heldout_views'], names[4:])
                self.assertEqual(summary['rounds'], 1)
                self.assertTrue((output / 'index.html').is_file())
                before = len(judge_calls), len(render_calls)
                search.finish(state, 'テスト終了')
                self.assertEqual((len(judge_calls), len(render_calls)), before)

    def test_judge_accepts_ties_and_rejects_bad_output(self):
        self.assertEqual(parse_judgment('```json\n{"winner":"tie","reason":"同程度"}\n```')['winner'], 'tie')
        with self.assertRaises(ValueError):
            parse_judgment('{"winner":"C","reason":"同程度"}')
