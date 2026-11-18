"""Authenticated lighting pair inference with a reloadable model registry."""
import argparse
import base64
import binascii
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import os
from pathlib import Path
import secrets
import threading


QUESTION = ('空・建物・路面の光の明るさ、色、影を見て、その場の照明としてより自然な方を選んでください。'
            '構図や建物の美しさは採点に含めません。最初の画像なら1、次の画像なら2、同程度なら3。'
            '半角数字の1、2、3のどれか1文字だけ答えてください。')
PROTOCOL = 'lighting-pair-v1'
MAX_BODY = 16 * 1024 * 1024


class ModelRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.cached = {}

    def read(self):
        registry = json.loads(self.path.read_text(encoding='utf-8-sig'))
        if not registry.get('models') or registry['default_model'] not in registry['models']:
            raise ValueError('Invalid model registry')
        return registry

    def resolve(self, name=None):
        registry = self.read()
        name = name or registry['default_model']
        if name not in registry['models']:
            raise ValueError('Unknown model name')
        record = registry['models'][name]
        base = Path(record['base_model']).expanduser().resolve(strict=True)
        adapter = Path(record['adapter']).expanduser().resolve(strict=True)
        edge = record.get('long_edge', 768)
        if type(edge) is not int or not 128 <= edge <= 1536:
            raise ValueError('Invalid image size')
        paths = [base / 'config.json', adapter / 'adapter_config.json', adapter / 'adapter_model.safetensors']
        stamps = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)
        cache_key = (stamps, edge)
        if cache_key not in self.cached:
            digest = hashlib.sha256((PROTOCOL + QUESTION + str(edge)).encode())
            for path in paths:
                digest.update(str(path).encode())
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
            self.cached[cache_key] = digest.hexdigest()
        return {'id': name, 'revision': self.cached[cache_key], 'protocol': PROTOCOL,
                'base_model': str(base), 'adapter': str(adapter), 'long_edge': edge}


class PairEngine:
    def __init__(self):
        self.base_path = None
        self.model = None
        self.active_revision = None

    def load(self, selected):
        os.environ['HF_DEACTIVATE_ASYNC_LOAD'] = '1'
        import torch
        from peft import PeftModel
        from transformers import AutoModelForImageTextToText, AutoProcessor, modeling_utils

        if not torch.cuda.is_available():
            raise RuntimeError('CUDA GPU unavailable')
        if self.active_revision == selected['revision']:
            return
        if isinstance(self.model, PeftModel):
            self.model = self.model.unload()
        self.active_revision = None
        if self.model is None or self.base_path != selected['base_model']:
            self.model = None
            torch.cuda.empty_cache()
            total = torch.cuda.get_device_properties(0).total_memory
            torch.cuda.set_per_process_memory_fraction(min(64 * 2**30 / total, 1.), 0)
            processor = AutoProcessor.from_pretrained(selected['base_model'], local_files_only=True)
            original = modeling_utils.safe_open
            modeling_utils.safe_open = lambda *a, **kw: original(*a, **(kw | {'backend': 'pread'}))
            try:
                self.model = AutoModelForImageTextToText.from_pretrained(selected['base_model'],
                    dtype=torch.bfloat16, device_map={'': 0}, local_files_only=True)
            finally:
                modeling_utils.safe_open = original
            self.processor = processor
            self.base_path = selected['base_model']
        config = json.loads((Path(selected['adapter']) / 'adapter_config.json').read_text())
        if Path(config['base_model_name_or_path']).name != Path(self.base_path).name:
            raise ValueError('Adapter base model does not match registry')
        self.model = PeftModel.from_pretrained(self.model, selected['adapter'], is_trainable=False,
                                               local_files_only=True)
        self.model.requires_grad_(False).eval()
        ids = [self.processor.tokenizer.encode(str(i), add_special_tokens=False) for i in (1, 2, 3)]
        if any(len(row) != 1 for row in ids) or len({row[0] for row in ids}) != 3:
            raise ValueError('Answer tokens are not unique single tokens')
        self.answer_ids = [row[0] for row in ids]
        self.active_revision = selected['revision']
        logging.info('Loaded model %s revision %s', selected['id'], selected['revision'][:12])

    def compare(self, selected, payload):
        import torch
        from PIL import Image, ImageOps

        pictures = []
        for data in payload['images']:
            try:
                raw = base64.b64decode(data, validate=True)
                with Image.open(io.BytesIO(raw)) as source:
                    if source.width * source.height > 16_000_000:
                        raise ValueError('Image exceeds pixel limit')
                    picture = ImageOps.exif_transpose(source).convert('RGB')
                picture.thumbnail((selected['long_edge'], selected['long_edge']), Image.Resampling.LANCZOS)
                pictures.append(picture)
            except (OSError, binascii.Error) as exc:
                raise ValueError('Invalid image') from exc
        self.load(selected)
        messages = [{'role': 'user', 'content': [
            {'type': 'text', 'text': '画像1'}, {'type': 'image'},
            {'type': 'text', 'text': '画像2'}, {'type': 'image'},
            {'type': 'text', 'text': payload['context'] + '\n' + QUESTION}]}]
        prompt = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True,
                                                     enable_thinking=False)
        inputs = self.processor(text=[prompt], images=pictures, return_tensors='pt').to('cuda:0')
        if inputs['image_grid_thw'].shape[0] != 2:
            raise RuntimeError('Expected two images')
        with torch.inference_mode():
            logits = self.model(**inputs, use_cache=False, logits_to_keep=1).logits[0, -1].float()
            if not torch.isfinite(logits).all().item():
                raise RuntimeError('Nonfinite inference result')
            answer = int(logits[self.answer_ids].argmax().item()) + 1
        return {'choice': answer, 'winner': {1: 'A', 2: 'B', 3: 'tie'}[answer],
                'model': selected['id'], 'revision': selected['revision'], 'protocol': PROTOCOL}


def make_server(address, registry, token, engine=None):
    engine = engine or PairEngine()
    # Avoid overlapping model swaps and GPU inference. Return busy to callers
    # instead of accumulating unbounded requests on the GPU.
    inference_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            supplied = self.headers.get('X-Lighting-API-Token')
            if supplied is None:
                supplied = self.headers.get('Authorization', '').removeprefix('Bearer ')
            if not hmac.compare_digest(supplied, token):
                self.reply(401, {'error': 'authentication_required'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            try:
                if self.path == '/health':
                    return self.reply(200, {'status': 'ok', 'protocol': PROTOCOL})
                if self.path == '/v1/models':
                    data = registry.read()
                    models = []
                    for name in data['models']:
                        selected = registry.resolve(name)
                        models.append({key: selected[key] for key in ('id', 'revision', 'protocol')})
                    return self.reply(200, {'default_model': data['default_model'], 'models': models})
                self.reply(404, {'error': 'not_found'})
            except (ValueError, OSError, KeyError):
                self.reply(503, {'error': 'invalid_registry'})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != '/v1/compare':
                return self.reply(404, {'error': 'not_found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    return self.reply(413, {'error': 'invalid_body_size'})
                payload = json.loads(self.rfile.read(length))
                if (not isinstance(payload, dict) or not isinstance(payload.get('images'), list)
                        or len(payload['images']) != 2 or not all(isinstance(v, str) for v in payload['images'])
                        or not isinstance(payload.get('context'), str) or len(payload['context']) > 4000):
                    raise ValueError('Invalid pair payload')
                selected = registry.resolve(payload.get('model'))
                if payload.get('revision') != selected['revision']:
                    return self.reply(409, {'error': 'model_revision_changed'})
            except (ValueError, KeyError, TypeError, OSError):
                return self.reply(400, {'error': 'invalid_request_or_model'})
            if not inference_lock.acquire(blocking=False):
                return self.reply(503, {'error': 'model_busy'})
            try:
                result = engine.compare(selected, payload)
                self.reply(200, result)
            except Exception as exc:
                logging.error('Inference failed: %s', type(exc).__name__)
                self.reply(503, {'error': 'inference_failed', 'type': type(exc).__name__})
            finally:
                inference_lock.release()

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            connection, remote = super().get_request()
            connection.settimeout(600)
            return connection, remote

    return Server(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=Path.home() / 'light_eval/lighting_api/models.json')
    parser.add_argument('--token-file', type=Path, default=Path.home() / 'light_eval/lighting_api/api.token')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=23002)
    parser.add_argument('--init-token', action='store_true')
    args = parser.parse_args()
    if args.init_token:
        args.token_file.parent.mkdir(parents=True, exist_ok=True)
        if not args.token_file.exists():
            fd = os.open(args.token_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(secrets.token_urlsafe(48) + '\n')
        print('API token file is ready.')
        return
    token = args.token_file.read_text().strip()
    if len(token) < 24:
        raise ValueError('API token is too short')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    server = make_server((args.host, args.port), ModelRegistry(args.registry), token)
    logging.info('Lighting API listening on port %d. Models load on first compare.', args.port)
    server.serve_forever()


if __name__ == '__main__':
    main()
