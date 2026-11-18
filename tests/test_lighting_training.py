"""Verify warm-starting and preservation without importing GPU libraries."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


path = Path(__file__).resolve().parents[1] / 'scripts/hpc/continue_lighting_training.py'
spec = importlib.util.spec_from_file_location('lighting_training', path)
training = importlib.util.module_from_spec(spec)
spec.loader.exec_module(training)


class LightingTrainingTests(unittest.TestCase):
    def test_warm_start_loads_existing_trainable_weights_and_records_their_identity(self):
        load = Mock(return_value='continued model')
        namespace = {'base_model': 'original model', 'INITIAL_ADAPTER': Path('saved_adapter'),
                     'INITIAL_DIGEST': 'known digest', 'PeftModel': SimpleNamespace(from_pretrained=load)}
        source = 'setup = {"seed": 42}\ntraining_model = get_peft_model(base_model, LoraConfig())'
        transform = training.Experiment(warm_start=True)
        exec(training.compile_cell(source, transform), namespace)
        load.assert_called_once_with('original model', 'saved_adapter', is_trainable=True, local_files_only=True)
        self.assertEqual(namespace['setup']['initial_weights_sha256'], 'known digest')
        self.assertEqual(namespace['training_model'], 'continued model')
        self.assertEqual(transform.replaced_warm_start, 1)

    def test_registration_preserves_original_selection_and_refuses_name_collisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lighting_api').mkdir()
            path = root / 'lighting_api/models.json'
            original = {'base_model': 'base', 'adapter': 'original', 'long_edge': 768}
            path.write_text(json.dumps({'default_model': 'a_all', 'models': {'a_all': original}}))
            training.register(root, 'extra', root / 'extra_adapter')
            result = json.loads(path.read_text())
            self.assertEqual(result['default_model'], 'a_all')
            self.assertEqual(result['models']['a_all'], original)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                training.register(root, 'a_all', root / 'wrong_adapter')
            self.assertEqual(path.read_bytes(), before)

    def test_service_pid_must_identify_our_program_before_stopping(self):
        with patch.object(Path, 'read_bytes', return_value=b'python\0unrelated.py\0'), patch.object(
                training.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'another program'):
                training.stop_service(Path('/lighting_api'), {'pid': 123})
            kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
