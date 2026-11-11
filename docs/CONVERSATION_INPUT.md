# 会話JSONから世界を作る

## Windowsのファイルから初期Blenderまで一括実行

ルートの `会話.json` の `content` に、国・町・街の説明・季節・時間帯を日本語で書きます。`.env` のWorldキーを設定して `会話から世界を作る.cmd` を起動するか、次を実行します。

```powershell
cd C:\Users\Astra\Desktop\prague
.\.venv\Scripts\python.exe -X utf8 run.py conversation-build
```

既存の `conversation.process(generate=False)` でHPCによる英語プロンプト生成・地名・標高・日時の選択まで進め、同じ条件を `build_world.execute()` へ渡します。気象・CAMS・World生成・HQ GLB・清掃・消灯・窓/街灯/ランタン検出・太陽/空の初期Blender・カメラ位置の土台まで自動実行します。本撮影は既定では行いません。

`--prompt-only` はHPCの抽出とプロンプト保存まで、`--dry-run` はAPIを使わないJSON形式確認です。`--with-capture` は道路探索と本撮影も追加します。引数に別の会話JSONのパスも指定できます。詳しい例は [会話から作るメモ](../会話から作るメモ.txt) を参照してください。

会話は `outputs/conversations/<会話本文のSHA-256>/` に保存し、その中の `build_handoff.json` が `outputs/builds/<日時>_conversation_<ID>/` を指します。会話で選んだ地点・日時をコピーするため、照明条件を除いた英語プロンプトから再推定しません。同じ会話は再開・結果表示となり、本文を変更すると新しい世界になります。既にこの会話で生成済みのWorldがあれば、その記録とモデルを再利用します。

この入口では場所・季節・時間帯を会話に記入してください。地名がなく既定プラハへフォールバックした場合も、World生成前に止めます。ローカルの `会話.json` はMac受信フォルダから分離し、Git対象外です。

## Macから会話JSONを受信する

Mac → Tailscale上のHTTP受信口 → `conversation_input/<conversation_id>.json` → HPC `qwen3.8:27b` → 世界説明・時間帯・季節・地名 → 地名検索（Open-Meteo）→ 既存の日付・時刻選択 → Blender用の条件ファイルとMarble用プロンプト、の順で処理します。

現在の受信URLは `http://100.105.169.99:8766/conversations` です。Tailscaleと受信サーバーの両方が必要です。Windowsの再起動・ログアウト後は受信サーバーを起動し直してください。Windowsログイン時の自動起動登録はしていません。

## Mac側の送信契約

```json
{
  "conversation_id": "854900a7-14f8-424e-b7dc-6561f85e81a6",
  "messages": [
    {"role": "assistant", "content": "どんな世界に行きたい？"},
    {"role": "user", "content": "秋の夕暮れのプラハ旧市街を歩きたい。"}
  ]
}
```

`conversation_id` はハイフン付きUUIDです。ファイル名はWindows側で決め、パス文字列は受け付けません。本文には全履歴を時系列で入れます。テキストだけに対応し、最大1MiBです。上限超過時も会話は切り捨てません。既存形式との互換用に `conversation_id` なしの `messages`、メッセージ配列だけの入力にも対応します。その場合のファイル名は会話本文のSHA-256です。

ヘッダーは次の2つです。

```text
Content-Type: application/json; charset=utf-8
Authorization: Bearer <受信トークン>
```

トークンはWindowsの `C:\Users\Astra\Desktop\prague\config\conversation_receiver.local.json` の `token` をMac側の送信設定へコピーしてください。APIキーとは別です。このファイルはGit対象外です。トークンなし・不一致は401になります。

認証に使うのはJSONの `token` フィールドの元の値です。照合用に伝えたSHA-256文字列をBearer値へ設定しないでください。Macのハッシュが「Windowsで表示されたハッシュ文字列をさらにSHA-256にした値」と一致する場合は、ハッシュをトークン本体としてコピーした可能性があります。

401の切り分け用に、実行中の認証処理は `outputs/conversation_receiver/runtime.json` に起動PID・採用した設定元・トークンのSHA-256を保存します。認証対象のリクエストごとに `auth_audit.jsonl` へ、実行中トークンと受信Bearer値それぞれのSHA-256、送信元IP、認証成否を記録します。Authorizationヘッダー・トークン本体・会話本文はこの診断ログへ保存しません。Mac側で計算するハッシュは `Bearer ` を除いたトークン値そのもののUTF-8バイト列を対象にしてください。

```bash
# Mac: TOKENに受信トークンを設定し、実際のJSONファイルを指定
curl --fail-with-body \
  -H 'Content-Type: application/json; charset=utf-8' \
  -H "Authorization: Bearer $TOKEN" \
  --data-binary @conversation.json \
  http://100.105.169.99:8766/conversations
```

保存完了後に202を返します。応答の `id` は本文のSHA-256、`conversation_id` はMacのUUIDです。202は受信完了を意味し、生成完了ではありません。生成処理中も次のHTTP受信ができます。

```bash
# 接続だけの確認。認証不要。200なら受信サーバーが稼働
curl --max-time 5 http://100.105.169.99:8766/health

# 処理状態。UUIDまたはPOST応答のidを指定
curl -H "Authorization: Bearer $TOKEN" \
  http://100.105.169.99:8766/conversations/854900a7-14f8-424e-b7dc-6561f85e81a6
```

状態は `received_or_processing`、`ready`（生成準備済み）、`needs_review`（必須項目不足）、`generating`（Marble生成中）、`generated`、`error` です。ファイル保存直後の表示には時間差があります。タイムアウトする場合は、受信サーバーの起動、両端のTailscale接続、WindowsファイアウォールのTCP 8766許可、Tailnetのアクセスルールを確認します。HTTP 401ならネットワーク接続は成功しており、トークンの設定が必要です。

## Windowsでの起動

初期設定済みのこのPCでは `scripts/windows/receive_conversations.cmd` を起動します。バックグラウンドで待ち受け、JSONを受信するとHPCによる抽出・プロンプト・日時設定の保存まで行います。初期状態ではMarble生成を呼びません。`/health` の `automatic_generation: false` でも確認できます。

初回セットアップ・別PCでは次のコマンドを実行します。

```powershell
python run.py conversation init-receiver --host 100.105.169.99
python -u -X utf8 run.py conversation serve
```

`init-receiver` はトークン・待受IP・ポートをローカル設定へ保存します。再実行してもトークンは変えません。`--port` で8766以外も指定できます。`CONVERSATION_RECEIVER_TOKEN` 環境変数があれば設定ファイルより優先します。接続先IPはこのWindows PCのTailscale IPv4を指定してください。

抽出結果を確認した後、Marble生成まで進める場合は `--generate` を明示します。Marble生成には既存のWorld Labs APIキーとクレジットを使います。現在は抽出の検証のため無効にしています。

```powershell
python run.py conversation serve
python run.py conversation process config/conversation.example.json --seed 42
python run.py conversation watch
python run.py conversation watch --once
```

起動中の受信サーバーとHPCで、代表的な5種類の会話を実機テストするには次を実行します。このテストは `/health` が `automatic_generation: false` を返す場合だけ実行し、Marble生成が無効であることを確認します。結果・入力・抽出根拠は `outputs/conversation_tests/` に保存します。

```powershell
python -X utf8 scripts/test_conversation_extraction.py
```

`serve` はHTTP受信とフォルダ監視を両方行います。フォルダへ直接コピーする場合は一時拡張子 `.part` で転送し、完了後に `.json` へ名前を変えてください。同じフォルダに別の監視プロセスを同時起動しないでください。

停止するときは実行中のターミナルでCtrl+Cを押します。バックグラウンド起動時は `outputs/conversation_receiver/server.pid` のPIDに対し、PowerShellで `taskkill /PID <PID> /T /F` を実行します。`/T` はPython仮想環境の起動プロセスと子プロセスを一緒に止めるためです。稼働中の生成処理がある場合は、同じ会話の `process ... --generate` で保存済みのWorld Labs処理を再開できます。

## 抽出結果と既存処理への接続

HPC接続は既存の `hpc_settings()` を再利用します。`.env` の `LLM_KEY` または `LITELLM_API_KEY`、既存のHPC用env設定を使います。既定モデルは `qwen3.8:27b` に固定し、古い `LLM_MODEL` 設定は引き継ぎません。変更は `--model`、envファイル指定は `--env-file` です。OpenAIへの自動フォールバックはありません。

AIには全メッセージをデータとして渡します。後からのユーザー訂正を優先し、AIの提案だけでは採用しないよう指示します。抽出した世界説明・季節・時間帯・地名にはユーザー発言中の引用根拠を要求し、コードで実在を検証します。引用と抽出内容の意味的な一致はAIに依存します。

## Marble用プロンプト

Marbleには「場所の説明」と「光の指定」の2部構成で渡します。qwenが書くのは前半の場所の説明（英語、`extraction.json` の `marble_scene`）だけです。後半は `config/marble_lighting.txt` を一字一句そのまま付けます。世界ごとに光の条件をそろえて比較を公平にするためです。

- 光は曇り・影なし・均一に固定。時間帯・季節・太陽・空・天気は場所の説明に入れません（時間帯は後段の光のループが担当）。
- 細い通り・小さな広場などの狭い空間にする。広い街並みは奥ほど崩れやすいため。
- 人・車は入れない。街灯は消灯（固定部分の `Unlit street lanterns`）。

場所の説明に光・時間・天気・人・車・雰囲気の語（sunset、morning、light、shadow、wet、tourists、cars、cozy、atmosphere、warm など）や日本語が入っていたらコードで弾き、qwenに1回だけ修正を求めます。それでも違反なら `error` で止めます。雪・落ち葉などの季節の物体は、ユーザーが明示した場合だけ入れます。

地名は `place_name`（会話での表記）、`place_query`（検索用の英語名）、`place_country`（国コード）、`place_relation`（`set_in`＝その場所そのもの／`inspired_by`＝「〜みたいな」架空の街）として抽出します。架空の場所だけ・地名なしの場合は4項目ともnullです。

世界説明・時間帯・季節のどれかが不明なら `needs_review` にし、日付のランダム補完やMarble生成へ進みません。地名は必須ではありません。地名があるのに検索で候補がない、または国コードなしで別の国に同程度の同名都市がある場合は `needs_review`（`missing: ["place"]`）で止めます。月と季節の矛盾、場所・日付に存在しない時間帯なども既存の検証で停止します。足りない情報を会話に追加して再送してください。

出力先は `outputs/conversations/<会話本文のSHA-256>/` です。

| ファイル | 内容・接続先 |
| --- | --- |
| `conversation.json` | 元のJSON（メタデータ含む） |
| `context.json` | seed と `--location` の明示地点。再開時は固定 |
| `location.json` | 採用地点（緯度・経度・標高・タイムゾーン）、地点の決め方（`conversation_place` / `explicit` / `config_default`）、抽出した地名と根拠 |
| `location_search.json` | 地名検索の全候補と出典（地名がある場合） |
| `extraction.json` | 世界説明・時間帯・季節・地名・引用根拠・明示日時条件 |
| `backend.json` | HPCモデル・接続先。キーは保存しない |
| `world_prompt.txt` | Marble用プロンプト＝qwenが書いた英語の「場所の説明」（`marble_scene`）＋固定の光の指定（`config/marble_lighting.txt`）。Marbleへそのまま送信 |
| `marble_request.json` | Marbleリクエストの確認用 |
| `date_input.json` / `date_selection.json` | `resolve_date()` への条件と結果 |
| `time_input.json` / `time_selection.json` | `resolve_time()` への条件と結果。現地日時・UTC・太陽高度等 |
| `render_settings.json` | 既存の `render_views(settings=...)` / `capture --settings` に渡せる `sun` 設定（緯度・経度・日付・時刻・標高） |
| `scene_conditions.json` | **Blenderで世界を作るときの条件一式**。地名・地点・季節・時間帯・日付・現地時刻・UTC・太陽位置・seedと、大気・気象取得のコマンド |
| `status.json` | 処理状態・不足項目・エラー段階 |
| `world/` | 生成時のWorld Labs処理記録・ワールド情報・閲覧URL |

地点は「`--location` の明示指定 → 会話の地名を `geocoding.search_locations()` で検索 → 地名がなければ `config/time_selection.json` の既定プラハ」の順に決めます。候補の選び方は、タイムゾーンのある居住地（GeoNamesの `PPL*`）→ 名前の完全一致 → 人口最大です。座標は都市の代表地点で、生成される街の実在の所在地ではありません。

季節は既存の `resolve_date()`（半球は採用地点の緯度から）、時間帯は `resolve_time()`（採用地点の日の出・日没基準）に実際に渡します。明示的な日付・時刻も引き継ぎます。既定年は `config/date_selection.json` の2026年です。

`render_settings.json` と `scene_conditions.json` は設定の受け渡しまでです。既存Blenderファイルの変更・レンダリング・GLBエクスポート・大気データの取得は自動では行いません。CAMSの大気（`capture --atmosphere-reference` に必要）と気象は、保存済みの `time_selection.json` から次のように取得できます（`scene_conditions.json` の `commands` にも同じ文があります）。

```powershell
python -X utf8 run.py atmosphere --from-time outputs/conversations/<SHA-256>/time_selection.json
python -X utf8 run.py weather --from-time outputs/conversations/<SHA-256>/time_selection.json
```

地名追加より前の形式で抽出済み・未生成の会話を `process` し直すと、旧結果を `v1_before_place/` に移して抽出からやり直します（seedは維持）。`ready` 済みの会話は従来どおり再処理しません。生成した世界のメッシュが必要なら次のように取得できます。

```powershell
python run.py world mesh --run-dir outputs/conversations/<SHA-256>/world
```

## Blenderへの自動反映

`scripts/prague_lighting.py` をBlenderで実行すると、3Dビュー右の「Prague」タブに「会話の条件」欄が出ます。

- **最新の会話を反映**：`outputs/conversations/*/scene_conditions.json` のうち一番新しいものを読み、地名の緯度・経度・標高と、選ばれた日付・現地時刻で `set_sun()` を呼んで太陽と空を設定します。夜・深夜なら太陽円盤を消し、道路視点の計画がある準備済みシーンでは夜間ライトも点けます。
- **新しい会話を自動で反映**：ONの間、3秒ごとに新しい `scene_conditions.json` を確認し、受信・抽出が終わった会話を自動で反映します。レンダリング中は待ちます。失敗した場合はコンソールに表示し、同じファイルでは再試行しません。

これで「Macから送信 → 受信 → HPC抽出 → 日付・時刻 → Blenderの太陽・空」が自動でつながります。受信サーバー（`receive_conversations.cmd`）とBlenderの両方を起動しておいてください。コンソールからは `apply_conversation()` または `apply_conversation(r"...\scene_conditions.json")` でも実行できます。大気（エアロゾル・オゾン）はこの反映では変更せず、現在の値を維持します。

## 再送・失敗時

同じUUID・同じJSONのHTTP再送は受信ファイルを書き直しません。会話本文のSHA-256でも処理を識別するため、別のUUIDでも本文が同じなら抽出・生成を再利用します。同じUUIDで履歴が増えれば受信ファイルを更新し、更新後の本文を新しい処理として扱います。更新前の処理結果も保持します。

受信・失敗の記録は `outputs/conversations/receipts/`、バックグラウンドサーバーのログは `outputs/conversation_receiver/` です。失敗した入力は2秒ごとに再試行しません。接続等を直した後、`python run.py conversation process conversation_input/<UUID>.json --generate` で明示的に再開します。HPC抽出済みなら保存結果を再利用します。送信結果不明のMarble処理は既存のマーカーで止め、勝手に再POSTしません。

強制終了で `.processing` が残った場合は、処理中のプロセスがないことを確認して当該ファイルだけ削除してから再開してください。受信済みJSON・会話・出力はGit対象外です。`config/world_prompt.txt` や既存のワールドは変更しません。
