"""Exercise resumability and credit boundaries without making paid requests."""
from contextlib import redirect_stdout
import io
import json
import os
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streetlab import build_world as build


class BuildTests(unittest.TestCase):
    def setUp(self):
        parent = build.ROOT / 'scratch/tests/build'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root_patch = patch.object(build, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.key_patch=patch.object(build,'load_api_key',return_value='offline-test-key')
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def test_unfinished_job_is_reused_and_completed_job_starts_new_run(self):
        first = build.select_run()
        build.save(first / 'build.json', {'status': 'interrupted'})
        self.assertEqual(build.select_run(), first)
        self.assertEqual(build.select_run(resume=True), first)
        build.save(first / 'build.json', {'status': 'complete'})
        second = build.select_run()
        self.assertNotEqual(first, second)
        self.assertTrue((first / 'build.json').exists())

    def test_changed_prompt_starts_another_world_while_previous_world_is_preserved(self):
        first=build.select_run()
        build.save(first/'build.json',{'status':'interrupted'})
        (first/'world_prompt.txt').write_text('Vienna',encoding='utf-8')
        with redirect_stdout(io.StringIO()):
            second=build.select_run(prompt_text='Rothenburg')
        self.assertNotEqual(first,second)
        self.assertEqual((first/'world_prompt.txt').read_text(),'Vienna')
        self.assertEqual(build.select_run(resume=True,prompt_text='Rothenburg'),first)

    def test_world_key_is_pinned_for_running_process_and_restored_afterwards(self):
        with patch.dict(os.environ,{'WORLD_LABS_API_KEY':'before'}), \
                patch.object(build,'load_api_key',return_value='launch-key'):
            with build.pinned_world_key():
                self.assertEqual(os.environ['WORLD_LABS_API_KEY'],'launch-key')
            self.assertEqual(os.environ['WORLD_LABS_API_KEY'],'before')

    def test_inventory_is_called_once_and_reused_after_dark_scene_metadata_changes(self):
        from streetlab import scene_inventory
        prepared=self.root/'prepared'
        prepared.mkdir()
        (prepared/'street_clean.blend').write_bytes(b'stable geometry')
        (prepared/'street_relight.blend').write_bytes(b'initial dark scene')
        build.save(prepared/'views.json',{'positions':[[0,0,1]]})
        config={'inventory':{'enabled':True,'rounds':2,'views_per_position':8}}
        def detect(args):
            output=Path(args[args.index('--output')+1])
            build.save(output/'inventory.json',{'objects':[],'lighting_assigned':False,'materials_assigned':False})
            (output/'street_mapped.blend').write_bytes(b'markers')
            build.save(output/'result.json',{'windows':0,'fixtures':0,'lights_added':0,
                'materials_changed':False})
            return output
        with patch.object(scene_inventory,'main',side_effect=detect) as detector:
            first=build.run_inventory(self.root,prepared,config)
            (prepared/'street_relight.blend').write_bytes(b'now contains location metadata')
            second=build.run_inventory(self.root,prepared,config)
        detector.assert_called_once()
        self.assertEqual(first,second)
        self.assertEqual((Path(first['inventory']).parent/'source.blend').read_bytes(),b'initial dark scene')

    def test_unknown_submissions_are_never_budgeted_as_new_requests(self):
        self.assertEqual(build.remaining_world_cost(self.root, 'marble-1.1'), 5080)
        self.assertEqual(build.remaining_world_cost(self.root, 'marble-1.1-plus'), 6580)
        (self.root / 'generate_submission_pending.json').write_text('{}')
        self.assertEqual(build.remaining_world_cost(self.root, 'marble-1.1'), 3500)
        (self.root / 'mesh_submission_pending.json').write_text('{}')
        self.assertEqual(build.remaining_world_cost(self.root, 'marble-1.1'), 0)

    def test_dry_run_has_no_calls_or_output_files(self):
        (self.root / 'config').mkdir()
        build.save(self.root / 'config/build_world.json', {'prompt_file': 'prompt.txt',
            'model': 'marble-1.1', 'date': '2026-07-15', 'time': '12:00'})
        (self.root / 'prompt.txt').write_text('A Prague street', encoding='utf-8')
        with patch.object(build, 'execute') as execute, patch.object(build.world, 'api') as api, \
                redirect_stdout(io.StringIO()):
            build.main(['--dry-run'])
        execute.assert_not_called()
        api.assert_not_called()
        self.assertFalse((self.root / 'outputs').exists())

    def test_resume_does_not_require_current_prompt_file(self):
        (self.root / 'config').mkdir()
        build.save(self.root / 'config/build_world.json', {'prompt_file': 'deleted.txt'})
        folder = build.select_run()
        build.save(folder / 'build.json', {'status': 'interrupted', 'config': {'original': True}})
        with patch.object(build, 'execute', return_value=folder) as execute, redirect_stdout(io.StringIO()):
            self.assertEqual(build.main(['--resume']), folder)
        execute.assert_called_once_with(folder)

    def test_full_glb_budget_is_checked_before_paid_generation(self):
        folder = self.root / 'job'
        folder.mkdir()
        location = {'latitude': 50.08804, 'longitude': 14.42076, 'elevation': 202,
                    'timezone': 'Europe/Prague'}
        build.save(folder / 'location.json', {'location': location})
        build.save(folder / 'atmosphere.json', {})
        build.save(folder / 'build.json', {'config': {'world_dir': str(folder / 'world'),
            'model': 'marble-1.1', 'date': '2026-07-15', 'time': '12:00', 'reference_year': 2025}})
        with patch.object(build.atmosphere_reference, 'plan_atmosphere'), \
                patch.object(build, 'load_api_key', return_value='offline-key'), \
                patch.object(build.world, 'api', return_value={'remaining_credits': 1920}), \
                patch.object(build.world, 'main') as paid, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, '残高が不足'):
                build.execute(folder)
        paid.assert_not_called()
        self.assertEqual(build.read(folder / 'build.json')['status'], 'interrupted')

    def test_downloaded_world_continues_locally_after_the_api_key_changes(self):
        folder=self.root/'cached'
        folder.mkdir()
        world_dir=folder/'world'
        world_dir.mkdir()
        chunk=b'{"asset":{"version":"2.0"}} '
        chunk+=b' '*(-len(chunk)%4)
        data=struct.pack('<4sII',b'glTF',2,20+len(chunk))+struct.pack('<I4s',len(chunk),b'JSON')+chunk
        (world_dir/'street_textured.glb').write_bytes(data)
        build.save(folder/'location.json',{'location':{'latitude':48.2,'longitude':16.37,
            'elevation':171,'timezone':'Europe/Vienna'}})
        build.save(folder/'atmosphere.json',{})
        build.save(folder/'build.json',{'config':{'world_dir':str(world_dir),'model':'marble-1.1',
            'date':'2026-07-15','time':'12:00','reference_year':2025}})
        with patch.object(build.atmosphere_reference,'plan_atmosphere'), \
                patch.object(build.world,'api') as api,patch.object(build.world,'main') as remote, \
                patch.object(build.prepare_world,'main',side_effect=RuntimeError('local cleanup reached')) as cleanup, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'local cleanup reached'):
                build.execute(folder)
        api.assert_not_called()
        remote.assert_not_called()
        cleanup.assert_called_once()

    def test_hpc_extraction_and_location_are_connected_before_generation(self):
        folder = self.root / 'auto_place'
        folder.mkdir()
        (folder / 'world_prompt.txt').write_text('A street in Vienna', encoding='utf-8')
        extraction = {'place_query': 'Vienna', 'place_country': 'AT'}
        location = {'latitude': 48.2, 'longitude': 16.37, 'elevation': 171, 'timezone': 'Europe/Vienna'}
        build.save(folder / 'atmosphere.json', {})
        build.save(folder / 'build.json', {'config': {'world_dir': str(folder / 'world'),
            'model': 'marble-1.1', 'date': '2026-07-15', 'time': '12:00', 'reference_year': 2025}})
        with patch.object(build.conversation, 'extract', return_value=(extraction, {'backend': 'hpc'})) as extract, \
                patch.object(build.conversation, 'resolve_location', return_value=(location, {'location': location})) as resolve, \
                patch.object(build.atmosphere_reference, 'plan_atmosphere'), \
                patch.object(build, 'load_api_key', return_value='offline-key'), \
                patch.object(build.world, 'api', return_value={'remaining_credits': 0}), \
                patch.object(build.world, 'main') as paid, redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                build.execute(folder)
        resolve.assert_called_once_with(extraction, None, folder)
        extract.assert_called_once_with([{'role':'user','content':'A street in Vienna'}],require_marble_scene=False)
        self.assertEqual(build.read(folder / 'extraction.json'), extraction)
        self.assertEqual(build.read(folder / 'location.json')['location']['elevation'], 171)
        paid.assert_not_called()

    def test_missing_preview_is_detected_for_resume(self):
        folder = self.root
        for name in ('dark.blend', 'dark.glb', 'initial.blend'):
            (folder / name).write_bytes(b'fixture')
        build.save(folder / 'settings.json', {'views': [{'image': 'view.png'}]})
        build.save(folder / 'result.json', {'dark_blend': str(folder / 'dark.blend'),
            'dark_glb': str(folder / 'dark.glb'), 'initial_blend': str(folder / 'initial.blend'),
            'render_settings': str(folder / 'settings.json')})
        self.assertFalse(build.result_exists(folder))
        (folder / 'view.png').write_bytes(b'fixture')
        self.assertTrue(build.result_exists(folder))

    def test_initial_only_completion_needs_no_photos_but_does_not_skip_requested_capture(self):
        folder=self.root
        for name in ('dark.blend','dark.glb','initial.blend','camera.json'):
            (folder/name).write_bytes(b'fixture')
        build.save(folder/'build.json',{'config':{'capture_enabled':False}})
        build.save(folder/'result.json',{'dark_blend':str(folder/'dark.blend'),
            'dark_glb':str(folder/'dark.glb'),'initial_blend':str(folder/'initial.blend'),
            'capture_enabled':False,'render_settings':None,'views':[],
            'camera_plan_file':str(folder/'camera.json')})
        self.assertTrue(build.result_exists(folder))
        self.assertFalse((folder/'renders').exists())
        build.save(folder/'build.json',{'config':{'capture_enabled':True}})
        self.assertFalse(build.result_exists(folder))

    def test_resume_can_change_capture_mode_without_changing_the_saved_world(self):
        (self.root/'config').mkdir()
        build.save(self.root/'config/build_world.json',{'prompt_file':'deleted.txt'})
        folder=build.select_run()
        build.save(folder/'build.json',{'status':'interrupted','config':{
            'world_dir':'original_world','roads':{'enabled':True,'eye':.7}}})
        with patch.object(build,'execute',return_value=folder),redirect_stdout(io.StringIO()):
            build.main(['--resume','--initialize-only'])
        saved=build.read(folder/'build.json')['config']
        self.assertEqual(saved['world_dir'],'original_world')
        self.assertFalse(saved['capture_enabled'])
        self.assertFalse(saved['roads']['enabled'])
        self.assertEqual(saved['roads']['eye'],.7)
        with patch.object(build,'execute',return_value=folder),redirect_stdout(io.StringIO()):
            build.main(['--resume','--with-capture'])
        self.assertTrue(build.read(folder/'build.json')['config']['roads']['enabled'])

    def test_second_process_cannot_run_the_same_job(self):
        with build.run_lock(self.root):
            with self.assertRaises(Exception):
                with build.run_lock(self.root):
                    self.fail('The second process acquired an exclusive job lock')

    def test_export_without_final_hpc_review_is_not_complete_cleanup(self):
        checkpoint=self.root/'checkpoint'
        polished=self.root/'polished'
        checkpoint.mkdir()
        polished.mkdir()
        for name in ('street_relight.blend','street_relight.glb','relight_report.json','views.json'):
            (polished/name).write_bytes(b'fixture')
        build.save(checkpoint/'preparation.json',{'polished':str(polished)})
        self.assertFalse(build.cleanup_complete(checkpoint,polished))
        build.save(checkpoint/'preparation.json',{'polished':str(polished),'status':'complete'})
        self.assertTrue(build.cleanup_complete(checkpoint,polished))

    def test_prompt_season_and_time_use_existing_date_time_selection(self):
        folder=self.root
        build.save(folder/'extraction.json',{'season':'winter','time_of_day':'night',
            'date_constraints':{'month':12},'time_constraints':{}})
        location={'latitude':50.08804,'longitude':14.42076,'elevation':202,'timezone':'Europe/Prague'}
        when=build.resolve_scene_time({'datetime_from_prompt':True,'date':'2026-07-15','time':'12:00'},location,folder)
        self.assertEqual(when.month,12)
        self.assertLess(build.read(folder/'time_selection.json')['solar_position']['elevation_deg'],0)

    def test_explicit_date_time_wins_over_prompt_conditions(self):
        folder=self.root
        build.save(folder/'extraction.json',{'season':'winter','time_of_day':'night'})
        location={'latitude':50.08804,'longitude':14.42076,'elevation':202,'timezone':'Europe/Prague'}
        when=build.resolve_scene_time({'datetime_from_prompt':True,'datetime_override':True,
            'date':'2026-07-15','time':'12:00'},location,folder)
        self.assertEqual(when.isoformat(),'2026-07-15T12:00:00+02:00')

    def test_existing_world_option_rejects_an_unsubmitted_request(self):
        (self.root / 'config').mkdir()
        build.save(self.root / 'config/build_world.json', {'prompt_file': 'unused.txt'})
        (self.root / 'world').mkdir()
        build.save(self.root / 'world/generate_request.json', {})
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            build.main(['--world-dir', str(self.root / 'world')])


if __name__ == '__main__':
    unittest.main()
