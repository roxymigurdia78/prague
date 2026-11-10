"""Check local conversation handoff without generating or exporting a paid world."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streetlab import conversation_build as local
from streetlab import build_world as build


class LocalConversationBuildTests(unittest.TestCase):
    def setUp(self):
        config=build.read(build.ROOT/'config/build_world.json')
        parent=build.ROOT/'scratch/tests/local_conversation';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'config').mkdir()
        build.save(self.root/'config/build_world.json',config)
        self.source=self.root/'会話.json'
        self.source.write_text(json.dumps({'messages':[{'role':'user','content':'冬の夕方のプラハの石畳の街。'}]},ensure_ascii=False),encoding='utf-8-sig')
        for module in (local,build):
            mocked=patch.object(module,'ROOT',self.root);mocked.start();self.addCleanup(mocked.stop)
        mocked=patch.object(build,'load_api_key',return_value='offline-test-key');mocked.start();self.addCleanup(mocked.stop)
        self.directory=self.ready('a'*64)

    def ready(self,identifier):
        folder=self.root/'outputs/conversations'/identifier;folder.mkdir(parents=True)
        files={
            'conversation.json':{'messages':[{'role':'user','content':'冬の夕方のプラハ'}]},
            'extraction.json':{'place_name':'プラハ','season':'winter','time_of_day':'evening'},
            'backend.json':{'model':'offline-model'},
            'location.json':{'source':'conversation_place','location':{'name':'Prague','latitude':50.08804,
                'longitude':14.42076,'elevation':202,'timezone':'Europe/Prague'}},
            'date_selection.json':{'date':'2026-12-15','season':'winter'},
            'time_selection.json':{'local_datetime':'2026-12-15T18:00:00+01:00','period':'evening'},
        }
        for name,data in files.items():build.save(folder/name,data)
        (folder/'world_prompt.txt').write_text('A narrow cobblestone lane in Prague with stone houses.\nFixed neutral illumination.',encoding='utf-8')
        return folder

    def status(self,folder=None):
        folder=folder or self.directory
        return {'status':'ready','id':folder.name,'missing':[],'run_dir':str(folder)}

    def invoke(self,args=None):
        with redirect_stdout(io.StringIO()):
            return local.main(args or [])

    def test_bom_json_dry_run_never_calls_hpc_or_creates_a_build(self):
        with patch.object(local.conversation,'process') as process,patch.object(build,'execute') as execute:
            self.assertEqual(self.invoke(['--dry-run']),0)
        process.assert_not_called();execute.assert_not_called()
        self.assertFalse((self.root/'outputs/builds').exists())

    def test_handoff_preserves_hpc_location_datetime_and_neutral_prompt(self):
        with patch.object(local.conversation,'process',return_value=self.status()) as process,patch.object(build,'execute') as execute:
            self.assertEqual(self.invoke(),0)
        self.assertFalse(process.call_args.kwargs['generate'])
        folder=execute.call_args.args[0];job=build.read(folder/'build.json')
        self.assertEqual(build.read(folder/'time_selection.json')['local_datetime'],'2026-12-15T18:00:00+01:00')
        self.assertEqual(build.read(folder/'location.json')['location']['elevation'],202)
        self.assertEqual((folder/'world_prompt.txt').read_text(),(self.directory/'world_prompt.txt').read_text())
        self.assertEqual(job['config']['world_dir'],str(self.directory/'world'))
        self.assertFalse(job['config']['capture_enabled']);self.assertTrue(job['config']['inventory']['enabled'])
        self.assertEqual(job['config']['date'],'2026-12-15')
        self.assertFalse(job['config']['datetime_from_prompt'])

    def test_same_conversation_reuses_the_same_build_after_relaunch(self):
        with patch.object(local.conversation,'process',return_value=self.status()),patch.object(build,'execute') as execute:
            self.invoke();first=execute.call_args.args[0]
            self.invoke();second=execute.call_args.args[0]
        self.assertEqual(first,second)
        self.assertEqual(len(list((self.root/'outputs/builds').glob('*/build.json'))),1)

    def test_existing_generated_world_keeps_its_original_model_and_request(self):
        world_dir=self.directory/'world';world_dir.mkdir()
        request={'model':'marble-1.1-plus','world_prompt':{'text_prompt':(self.directory/'world_prompt.txt').read_text().strip()}}
        build.save(world_dir/'generate_request.json',request)
        before=(world_dir/'generate_request.json').read_bytes()
        with patch.object(local.conversation,'process',return_value=self.status()|{'status':'generated'}),patch.object(build,'execute') as execute:
            self.invoke()
        job=build.read(execute.call_args.args[0]/'build.json')
        self.assertEqual(job['config']['model'],'marble-1.1-plus')
        self.assertEqual(job['config']['world_dir'],str(world_dir))
        self.assertEqual((world_dir/'generate_request.json').read_bytes(),before)

    def test_missing_conversation_conditions_do_not_start_world_generation(self):
        status=self.status()|{'status':'needs_review','missing':['season','time_of_day']}
        with patch.object(local.conversation,'process',return_value=status),patch.object(build,'execute') as execute:
            self.assertEqual(self.invoke(),2)
        execute.assert_not_called();self.assertFalse((self.directory/'build_handoff.json').exists())

    def test_absent_place_does_not_silently_generate_the_default_city(self):
        location=build.read(self.directory/'location.json');location['source']='config_default'
        build.save(self.directory/'location.json',location)
        with patch.object(local.conversation,'process',return_value=self.status()),patch.object(build,'execute') as execute:
            with self.assertRaisesRegex(ValueError,'地名'):self.invoke()
        execute.assert_not_called()

    def test_prompt_only_calls_extraction_but_never_the_world_pipeline(self):
        with patch.object(local.conversation,'process',return_value=self.status()) as process,patch.object(build,'execute') as execute:
            self.assertEqual(self.invoke(['--prompt-only']),0)
        process.assert_called_once();execute.assert_not_called()
        self.assertFalse((self.directory/'build_handoff.json').exists())

    def test_capture_can_be_added_to_the_same_world(self):
        with patch.object(local.conversation,'process',return_value=self.status()),patch.object(build,'execute') as execute:
            self.invoke();first=execute.call_args.args[0]
            self.invoke(['--with-capture']);second=execute.call_args.args[0]
        self.assertEqual(first,second)
        config=build.read(first/'build.json')['config']
        self.assertTrue(config['capture_enabled']);self.assertTrue(config['roads']['enabled'])

    def test_changed_conversation_creates_a_separate_build_and_keeps_the_old_one(self):
        other=self.ready('b'*64)
        with patch.object(local.conversation,'process',side_effect=[self.status(),self.status(other)]),patch.object(build,'execute') as execute:
            self.invoke();first=execute.call_args.args[0]
            self.invoke();second=execute.call_args.args[0]
        self.assertNotEqual(first,second)
        self.assertTrue((first/'build.json').exists());self.assertTrue((second/'build.json').exists())


if __name__=='__main__':unittest.main()
