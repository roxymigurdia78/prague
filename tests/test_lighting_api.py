import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from streetlab.lighting_api_client import LightingAPI


script = Path(__file__).resolve().parents[1] / 'scripts/hpc/lighting_api.py'
spec = importlib.util.spec_from_file_location('lighting_api_server', script)
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


class LightingAPITests(unittest.TestCase):
    def test_model_switch_revision_authentication_and_two_direction_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, first, second = [root / name for name in ('base', 'first', 'second')]
            for path in (base, first, second):
                path.mkdir()
                (path / 'config.json').write_text('{}')
                (path / 'adapter_config.json').write_text('{}')
                (path / 'adapter_model.safetensors').write_text(path.name)
            config = {'default_model': 'current', 'models': {
                'current': {'base_model': str(base), 'adapter': str(first)},
                'future': {'base_model': str(base), 'adapter': str(second)}}}
            path = root / 'models.json'
            path.write_text(json.dumps(config))
            registry = server_module.ModelRegistry(path)
            token = 'test-token-which-is-long-enough'

            class Engine:
                def compare(self, selected, payload):
                    values = []
                    for raw in payload['images']:
                        with Image.open(io.BytesIO(base64.b64decode(raw))) as image:
                            values.append(image.getpixel((0, 0))[0])
                    winner = 'A' if values[0] > values[1] else 'B' if values[0] < values[1] else 'tie'
                    return {'winner': winner, 'model': selected['id'], 'revision': selected['revision'],
                            'protocol': server_module.PROTOCOL}

            server = server_module.make_server(('127.0.0.1', 0), registry, token, Engine())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}'
                with self.assertRaises(HTTPError) as error:
                    urlopen(url + '/v1/models')
                self.assertEqual(error.exception.code, 401)
                with self.assertRaisesRegex(ValueError, 'LIGHTING_MODEL'):
                    LightingAPI({'LIGHTING_API_URL': url, 'LIGHTING_API_TOKEN': token})
                connection = {'LIGHTING_API_URL': url, 'LIGHTING_API_TOKEN': token, 'LIGHTING_MODEL': 'current'}
                client = LightingAPI(connection)
                self.assertEqual(client.model, 'current')
                self.assertEqual(client.headers['User-Agent'], 'Streetlab-Lighting/1.0')
                hub_client = LightingAPI(connection | {'LIGHTING_JUPYTER_TOKEN': 'test-hub-token'})
                self.assertEqual(hub_client.model, 'current')
                self.assertEqual(hub_client.headers['Authorization'], 'token test-hub-token')
                self.assertEqual(hub_client.headers['X-Lighting-API-Token'], token)
                brighter, dimmer = root / 'bright.png', root / 'dark.png'
                Image.new('RGB', (30, 40), (180, 180, 180)).save(brighter)
                Image.new('RGB', (30, 40), (20, 20, 20)).save(dimmer)
                self.assertEqual(client(brighter, dimmer, '夕方')['winner'], 'A')
                self.assertEqual(client(dimmer, brighter, '夕方')['winner'], 'B')
                self.assertEqual(len(client(brighter, dimmer, '夕方')['directions']), 2)
                original_revision = client.revision
                config['models']['current']['adapter'] = str(second)
                path.write_text(json.dumps(config))
                self.assertNotEqual(registry.resolve('current')['revision'], original_revision)
                with self.assertRaisesRegex(RuntimeError, '409'):
                    client(brighter, dimmer, '夕方')
                fresh = LightingAPI(connection)
                self.assertEqual(fresh(brighter, dimmer, '夕方')['winner'], 'A')
                config['default_model'] = 'future'
                path.write_text(json.dumps(config))
                self.assertEqual(LightingAPI(connection).model, 'current')
                self.assertEqual(LightingAPI(connection, model='future').model, 'future')
                config['models']['future']['long_edge'] = 512
                before = registry.resolve('future')['revision']
                path.write_text(json.dumps(config))
                self.assertNotEqual(registry.resolve('future')['revision'], before)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
