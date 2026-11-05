"""World APIの接続と残高を確認する。追加ライブラリは不要。"""

import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from streetlab.paths import ROOT


def load_api_key():
    key = os.environ.get("WORLD_LABS_API_KEY", "").strip()
    if key:
        return key
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name.strip() == "WORLD_LABS_API_KEY":
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value.strip()
    return ""


def main():
    key = load_api_key()
    if not key:
        print(".env の WORLD_LABS_API_KEY= の後ろにキーを貼り付けて保存してください。")
        return 1

    request = Request(
        "https://api.worldlabs.ai/marble/v1/credits",
        headers={"WLT-Api-Key": key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        balance = result["remaining_credits"]
        if isinstance(balance, bool) or not isinstance(balance, (int, float)):
            raise ValueError("Invalid credit balance")
    except HTTPError as error:
        messages = {
            401: "キーの認証に失敗しました。キーを確認してください。",
            403: "このキーにはアクセス権がありません。",
            404: "APIが有効なアカウントか、World Labs Platformで確認してください。",
            429: "アクセスが集中しています。少し待って再実行してください。",
            503: "残高のサービスが一時的に利用できません。後で再実行してください。",
        }
        print(f"HTTP {error.code}: {messages.get(error.code, 'World APIへの接続に失敗しました。')}")
        return 1
    except (URLError, TimeoutError, OSError):
        print("接続できませんでした。ネットワーク接続を確認して再実行してください。")
        return 1
    except (ValueError, KeyError, TypeError):
        print("World APIから想定した残高データを取得できませんでした。")
        return 1

    print("World APIへの接続に成功しました。")
    print(f"API残高: {balance:,.0f} クレジット")
    print("このプログラムは残高照会のみを行います。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
