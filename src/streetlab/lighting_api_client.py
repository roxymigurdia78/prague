"""Client for the user's named, trained lighting models on HPC."""
import base64
import io
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


KEYS = ('LIGHTING_API_URL', 'LIGHTING_API_TOKEN', 'LIGHTING_MODEL',
        'LIGHTING_CF_CLIENT_ID', 'LIGHTING_CF_CLIENT_SECRET', 'LIGHTING_JUPYTER_TOKEN')


def settings(path):
    values = {}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            if key in KEYS:
                values[key] = value.strip().strip('"\'')
    values.update({key: os.environ[key].strip() for key in KEYS if key in os.environ})
    return values


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class LightingAPI:
    def __init__(self, config, model=None):
        name = (model or config.get('LIGHTING_MODEL') or '').strip()
        if not name:
            raise ValueError('.env に LIGHTING_MODEL=a_all のように使うモデル名を指定してください。')
        self.url = config['LIGHTING_API_URL'].rstrip('/')
        parsed = urlsplit(self.url)
        if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost'))
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('LIGHTING_API_URL は公開したHTTPSのURLを指定してください。')
        token = config.get('LIGHTING_API_TOKEN', '')
        if not token:
            raise ValueError('LIGHTING_API_TOKEN を設定してください。')
        self.headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                        'User-Agent': 'Streetlab-Lighting/1.0', 'Accept': 'application/json'}
        hub_token = config.get('LIGHTING_JUPYTER_TOKEN', '')
        if '/hub/user-api/' in parsed.path and not hub_token:
            raise ValueError('この公開URLはJupyterHub経由です。.envのLIGHTING_JUPYTER_TOKENにポータルのJupyterHub API Tokenを設定してください。')
        if hub_token:
            self.headers['Authorization'] = 'token ' + hub_token
            self.headers['X-Lighting-API-Token'] = token
        client_id, secret = config.get('LIGHTING_CF_CLIENT_ID'), config.get('LIGHTING_CF_CLIENT_SECRET')
        if bool(client_id) != bool(secret):
            raise ValueError('CloudflareのClient IDとSecretを両方設定してください。')
        if client_id:
            self.headers.update({'CF-Access-Client-Id': client_id, 'CF-Access-Client-Secret': secret})
        self.opener = build_opener(NoRedirect())
        models = self.request('/v1/models')
        selected = next((row for row in models['models'] if row['id'] == name), None)
        if selected is None or selected.get('protocol') != 'lighting-pair-v1':
            raise ValueError(f'公開APIに対応するモデルがありません: {name}')
        self.model = selected['id']
        self.revision = selected['revision']

    def request(self, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(3):
            request = Request(self.url + path, data=data, headers=self.headers)
            try:
                with self.opener.open(request, timeout=600) as response:
                    result = json.loads(response.read(1024 * 1024))
                return result
            except HTTPError as exc:
                if exc.code == 503 and attempt < 2:
                    time.sleep(2)
                    continue
                if exc.code == 403 and b'1010' in exc.read(32000):
                    raise RuntimeError('照明APIへの要求がCloudflareのクライアント識別チェック（1010）で拒否されました。') from None
                hint = {401: 'APIトークン', 403: 'JupyterHubトークン・公開URLの権限・Cloudflareの認証', 409: 'モデルの更新（新しい保存先で再開）',
                        302: 'Cloudflareの認証設定', 503: 'HPCのGPU・サービスのログ'}.get(exc.code, 'API設定')
                raise RuntimeError(f'照明APIで停止しました。HTTP {exc.code}。{hint}を確認してください。') from None
            except (URLError, TimeoutError, OSError):
                raise RuntimeError('照明APIに接続できません。公開URLとHPCの起動状態を確認してください。') from None
            except (ValueError, UnicodeError):
                raise RuntimeError('照明APIからJSONを受け取れません。公開URLを確認してください。') from None

    def compare(self, images, condition):
        result = self.request('/v1/compare', {'model': self.model, 'revision': self.revision,
                                             'images': images, 'context': condition})
        if (result.get('model') != self.model or result.get('revision') != self.revision
                or result.get('protocol') != 'lighting-pair-v1'
                or result.get('winner') not in ('A', 'B', 'tie')):
            raise RuntimeError('照明APIのモデル名・版・判定が開始時と一致しません。')
        return result

    def __call__(self, left, right, condition):
        from PIL import Image

        # The search stores the forward/backward views in two rows. Feed each
        # direction as a separate two-photo comparison, matching notebook inputs.
        panels = []
        for path in (left, right):
            with Image.open(path) as image:
                panels.append(image.convert('RGB'))
        if panels[0].size != panels[1].size or panels[0].height % 2:
            raise ValueError('比較用の前後画像のサイズが一致しません。')
        directions = []
        height = panels[0].height // 2
        for index in range(2):
            images = []
            for panel in panels:
                stream = io.BytesIO()
                panel.crop((0, index * height, panel.width, (index + 1) * height)).save(stream, format='PNG')
                images.append(base64.b64encode(stream.getvalue()).decode())
            directions.append(self.compare(images, condition))
        winners = [row['winner'] for row in directions]
        winner = winners[0] if winners[0] == winners[1] else 'tie'
        return {'winner': winner, 'reason': f'前方 {winners[0]}・後方 {winners[1]}',
                'model': self.model, 'revision': self.revision, 'directions': directions}


def main(argv=None):
    import argparse
    from streetlab.paths import ROOT

    parser = argparse.ArgumentParser(description='照明APIに接続し、使うモデル名と版を確認')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--model')
    args = parser.parse_args(argv)
    config = settings(args.env_file)
    if not config.get('LIGHTING_API_URL'):
        parser.error('.env に LIGHTING_API_URL を設定してください。')
    try:
        client = LightingAPI(config, args.model)
        print('接続成功。モデル:', client.model, '版:', client.revision[:12])
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        parser.exit(1, f'停止: {error}\n')


if __name__ == '__main__':
    main()
