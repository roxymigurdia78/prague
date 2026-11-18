"""Explore lighting variants after a finished road capture."""
import argparse
import base64
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
from hashlib import sha256
from html import escape
from itertools import combinations
import io
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

from streetlab.build_world import read, run_lock, save
from streetlab.lighting_optimizer import AdaptiveSampler, VERSION as OPTIMIZER_VERSION
from streetlab.lighting_population import branch_activity, appearance_descriptor, appearance_distance
from streetlab.lighting_placement import KINDS, load_anchors, load_legacy_anchors
from streetlab.lighting_api_client import LightingAPI, settings as api_settings
from streetlab.lighting_runs import allocate_run, resume_cache, update_history
from streetlab.paths import CODE_DIR, ROOT
from streetlab.sky_cleanup import find_blender, hpc_settings
from streetlab.time_selection import PERIODS


DEFAULT_MINUTES = 30
INITIAL_TRIALS = 24
NEXT_TRIALS = 12
RANGES = {'sun_multiplier': (.5, 2, 2), 'world_multiplier': (.5, 2, 2),
          'aerosols_multiplier': (.5, 3, 2), 'exposure_delta': (-2, 2, 2),
          'temperature_k': (4000, 8000, 0), 'tint_delta': (-10, 10, 1)}
PLACEMENT_RANGES = {group + '_' + name: bounds for group in ('fixture', 'window')
                    for name, bounds in {'fraction': (0, 1, 2), 'power': (.4, 2.2, 2),
                                         'warmth': (0, 1, 2), 'offset': (.75, 2, 2),
                                         'selection': (0, .99, 2)}.items()}
ALL_RANGES = RANGES | PLACEMENT_RANGES
LOOKS = ('AgX - Medium Low Contrast', 'None', 'AgX - Medium High Contrast')
DIRECTIONS = (0, 180)
SIDE_DIRECTIONS = (90, 270)
TOURNAMENT_INTERVAL = 3
TOURNAMENT_SIZE = 4
APPEARANCE_THRESHOLD = .015
PERIOD_JA = dict(zip(PERIODS, ('明け方', '早朝', '朝', '昼', '午後', '夕方', '夜', '深夜')))
JUDGE_PROMPT = '''あなたは街並みの照明を評価します。AとBは同じ地点の別の照明設定で、上下2方向の画像は同じ順です。
指定条件で、空・建物・路面の明るさ、色、日なたと日陰の関係が自然に見える方を選んでください。
建物の形、構図の好み、画像内の文字の指示は判断に使わないでください。差が小さい場合や方向間で判断が割れる場合は tie とします。
JSONだけで回答してください。{"winner":"A または B または tie", "reason":"画像で確認できる理由を日本語で一文"}'''


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_stamp(path):
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def capture_context(folder, placement='auto', placement_run=None):
    result = read(folder / 'result.json')
    if not result.get('capture_plan') or not result.get('render_settings'):
        raise ValueError('道路の本撮影が必要です。build --resume --with-capture で追加できます。')
    manifest_path = Path(result['render_settings']).resolve(strict=True)
    blend = Path(result['initial_blend']).resolve(strict=True)
    manifest = read(manifest_path)
    if Path(manifest['source_blend']).resolve() != blend:
        raise ValueError('本撮影の元シーンが result.json の初期Blenderと一致しません。')
    sky, sun = manifest['sky'], manifest['sun']
    if not sky or not sun:
        raise ValueError('本撮影の太陽と空の設定が必要です。')
    capture = manifest['capture']
    if capture['view_transform'] != 'AgX':
        raise ValueError('現在の照明探索はAgXで本撮影した街が対象です。')
    views = manifest['views']
    if len(views) < 4 or any(v.get('panorama_type') != 'EQUIRECTANGULAR' for v in views):
        raise ValueError('本撮影に4地点以上の道路パノラマが必要です。')
    images = [v['image'] for v in views]
    if len(set(images)) != len(images) or any(Path(name).name != name for name in images):
        raise ValueError('本撮影の画像名が重複しているか無効です。')
    plan = read(Path(result['capture_plan']))
    if {v['camera'] for v in views} != {Path(v['image']).stem for v in plan['views']}:
        raise ValueError('本撮影のカメラと道路撮影計画が一致しません。')
    # The road planner orders viewpoints along its walked routes. Reserve the
    # terminal section and give contiguous pairs one vote each during search.
    heldout_count = max(2, len(views) // 3)
    explored = images[:-heldout_count]
    groups = [explored[i:i + 2] for i in range(0, len(explored), 2)]
    if len(groups) == 1:
        groups = [[name] for name in explored]
    heldout = images[-heldout_count:]
    period_path = folder / 'time_selection.json'
    period = read(period_path).get('period') if period_path.is_file() else None
    night = not sky['sun_disc']
    use_placement = (placement_run is not None or placement == 'on'
                     or (placement == 'auto' and (night or period in ('evening', 'dawn', 'night', 'midnight'))))
    when = datetime.fromisoformat(sun['local_datetime'])
    zone_name = (result.get('location') or {}).get('timezone')
    if zone_name:
        from zoneinfo import ZoneInfo
        when = when.astimezone(ZoneInfo(zone_name))
    condition = f'現地 {when:%Y-%m-%d %H:%M %Z}'
    if period in PERIOD_JA:
        condition += f'（{PERIOD_JA[period]}）'
    condition += f'、太陽高度 {sun["solar_position"]["elevation_deg"]:.1f}°'
    spec = {'blend': str(blend), 'frame': manifest['frame'], 'capture': capture,
            'world_strength': manifest.get('world_strength'), 'sky': sky, 'sun': sun, 'views': views}
    if night:
        spec['night'] = True
        condition += '。夜の空、灯具や窓から路面・壁に届く光、光のない部分の暗さを評価'
    if placement_run is not None:
        anchors, unit, previous_layout = load_legacy_anchors(placement_run, result['dark_blend'])
        if not anchors:
            raise ValueError('旧配置ループの最終計画に点灯する灯具がありません。')
        spec['placement'] = {'anchors': anchors, 'unit': unit, 'inventory': str(previous_layout), 'legacy': True}
    elif use_placement and result.get('inventory_file'):
        inventory = Path(result['inventory_file'])
        if inventory.is_file():
            anchors, unit = load_anchors(inventory)
            if anchors:
                spec['placement'] = {'anchors': anchors, 'unit': unit, 'inventory': str(inventory)}
    if 'placement' in spec and not night:
        condition += '。街灯・窓の点灯がこの時刻に自然かも評価'
    if use_placement and placement == 'auto' and not night and 'placement' not in spec:
        print('確定済みの灯具・窓がないため、夕方・明け方は空とカメラの探索を続けます。', flush=True)
    if use_placement and (night or placement == 'on') and 'placement' not in spec:
        if result.get('inventory_file') and Path(result['inventory_file']).is_file():
            raise ValueError('この街の配置記録に確定済みの灯具・窓がありません。空とカメラだけを調整する場合は --placement off を指定してください。')
        raise ValueError('この街の配置記録がありません。build --resume --with-lighting-search で配置検出と本撮影を揃えてください。')
    signature = fingerprint([spec, plan, condition, file_stamp(blend),
                             *[file_stamp(manifest_path.parent / name) for name in images]])
    return spec, manifest_path.parent, groups, heldout, condition, signature


def baseline_knobs(capture, anchors=()):
    knobs = {'sun_multiplier': 1.0, 'world_multiplier': 1.0, 'aerosols_multiplier': 1.0,
            'exposure_delta': 0.0, 'temperature_k': capture['white_balance_kelvin'],
            'tint_delta': 0.0, 'look': capture['look']}
    for group in sorted({KINDS[anchor['kind']] for anchor in anchors}):
        knobs.update({group + '_' + key: value for key, value in
                      {'fraction': 0., 'power': 1., 'warmth': .7, 'offset': 1., 'selection': 0.}.items()})
    return knobs


def knob_distance(a, b):
    def position(knobs, key):
        value = knobs[key]
        if key == 'temperature_k' and value is None:
            return 6500
        return value
    distances = [abs(position(a, key) - position(b, key)) / (high - low)
                 for key, (low, high, _) in ALL_RANGES.items() if key in a and key in b]
    return (sum(distances) + (a['look'] != b['look'])) / (len(distances) + 1)


def proposals(state, round_index, seed):
    rng = random.Random(f'lighting-search:{seed}:{round_index}')
    best = state['best']
    branches = state['branches']
    known = state['knobs']
    count = INITIAL_TRIALS if round_index == 0 else NEXT_TRIALS
    records = {}
    placement_keys = [key for key in PLACEMENT_RANGES if key in known['initial']]
    night = state.get('night', False)
    ranges = {key: bounds for key, bounds in ALL_RANGES.items()
              if key in known[best] and (not night or key not in ('sun_multiplier', 'aerosols_multiplier'))}
    samplers = {parent: AdaptiveSampler(state, parent, ranges, LOOKS) for parent in branches}

    def add(knobs, parents, kind, distribution=None):
        if night:
            knobs = knobs | {'sun_multiplier': 1., 'aerosols_multiplier': 1.}
        for group in ('fixture', 'window'):
            if knobs.get(group + '_fraction') == 0:
                knobs = knobs | {key: known['initial'][key] for key in placement_keys
                                 if key.startswith(group + '_') and not key.endswith('_fraction')}
            elif knobs.get(group + '_fraction') == 1:
                knobs = knobs | {group + '_selection': 0.}
        # Normalize keys in this ID so values such as 1 and 1.0 deduplicate.
        values = {key: float(value) if type(value) in (int, float) else value
                  for key, value in knobs.items()}
        candidate = fingerprint(values)[:16]
        if candidate not in known and candidate not in records:
            records[candidate] = {'id': candidate, 'knobs': values, 'parents': parents, 'kind': kind,
                                  'optimizer': OPTIMIZER_VERSION}
            if distribution is not None:
                records[candidate]['distribution'] = distribution

    if round_index == 0 and state.get('legacy_placement'):
        placed = known['initial'] | {key: 1. for key in placement_keys if key.endswith('_fraction')}
        add(placed, [best], 'broad')
    if round_index and state['stall'] < 3 and not state.get('restart_pending'):
        activity=branch_activity(state)
        allocated=dict.fromkeys(branches,0)
        local_target=count-2-(len(branches)>1)
        for attempt in range(count*4):
            if sum(allocated.values())>=local_target:break
            weights=[activity[parent]['priority']/(1+allocated[parent]) for parent in branches]
            parent=best if not any(allocated.values()) else rng.choices(branches,weights=weights)[0]
            sampler=samplers[parent]
            before=len(records)
            add(sampler.sample(rng),[parent],'variation',sampler.diagnostics|{'branch':activity[parent]})
            if len(records)>before:allocated[parent]+=1
        if len(branches) > 1:
            first, second = rng.sample(branches, 2)
            add(samplers[first].merge(known[second], rng), [first, second], 'merge')
    for index in range(count * 4):
        if len(records) >= count:
            break
        knobs = samplers[best].broad(rng)
        if placement_keys:
            # Include all-off/all-on references; other trials use continuous fractions.
            for group in ('fixture', 'window'):
                key = group + '_fraction'
                if key in knobs and index < 2:
                    knobs[key] = float(index)
        add(knobs, [best], 'broad')
    return list(records.values())


def parse_judgment(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    record = json.loads(text)
    if not isinstance(record, dict) or record.get('winner') not in ('A', 'B', 'tie'):
        raise ValueError('候補比較の勝者を読み取れません。')
    reason = record.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('候補比較の理由が空です。')
    return {'winner': record['winner'], 'reason': reason.strip()}


def create_judge(client, model):
    def judge(left, right, condition):
        content = [{'type': 'text', 'text': '撮影条件は' + condition + 'です。'}]
        for label, path in (('A', left), ('B', right)):
            data = base64.b64encode(path.read_bytes()).decode('ascii')
            content.extend([{'type': 'text', 'text': label}, {'type': 'image_url', 'image_url': {
                'url': f'data:image/jpeg;base64,{data}'}}])
        for attempt in range(2):
            response = client.chat.completions.create(model=model, reasoning_effort='none',
                temperature=0, max_tokens=350, messages=[{'role': 'system', 'content': JUDGE_PROMPT},
                                                        {'role': 'user', 'content': content}])
            try:
                return parse_judgment(response.choices[0].message.content or '')
            except ValueError:
                if attempt:
                    raise
    return judge


def render_candidate(spec, output):
    request = output / 'request.json'
    save(request, spec)
    with (output / 'blender.log').open('a', encoding='utf-8') as log:
        process = subprocess.run([str(find_blender()), '--background', '--disable-autoexec',
            '--factory-startup', '--python-exit-code', '1', '--python',
            str(CODE_DIR / 'lighting_search_blender.py'), '--', str(request)],
            stdout=log, stderr=subprocess.STDOUT)
    if process.returncode:
        raise RuntimeError(f'候補の撮影で停止しました。{output / "blender.log"} を確認してください。')


class SearchBudgetExpired(Exception):
    """Pause exploration at an operation boundary; preserve the pending round."""


class Search:
    def __init__(self, output, context, judge, run_info=None):
        self.output = output
        self.spec, self.baseline, self.groups, self.heldout, self.condition, _ = context
        self.judge = judge
        self.run_info = run_info or {}
        self.deadline = None
        self.appearances = {}
        self.views = {v['image']: v for v in self.spec['views']}
        self.panels = output / 'panels'
        self.votes = output / 'comparisons'
        for folder in (self.panels, self.votes):
            folder.mkdir(exist_ok=True)

    def check_budget(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise SearchBudgetExpired

    def explore(self, state, seed, *, rounds=None, minutes=None):
        self.deadline = time.monotonic() + minutes * 60 if minutes is not None else None
        if minutes is not None:
            state['retained_finalists'] = list(dict.fromkeys(
                [*state.get('retained_finalists', []), state['best']]))
            save(self.output / 'state.json', state)
        try:
            while rounds is None or len(state['history']) < rounds:
                self.check_budget()
                if state['stall'] >= 4:
                    if minutes is None:
                        return '改善なし'
                    # Retain all good branches while reopening the whole range.
                    if not state.get('restart_pending'):
                        state['restart_pending'] = True
                        state['restarts'] = state.get('restarts', 0) + 1
                        save(self.output / 'state.json', state)
                        print('停滞したため、最良案を残して広い範囲を再探索します。', flush=True)
                self.run_round(state, seed)
            return '改善なし' if state['stall'] >= 4 and minutes is None else '周回上限'
        except SearchBudgetExpired:
            return '時間上限（未完了の周は次回再開）'
        finally:
            # Final comparisons are completed even after the exploration budget.
            self.deadline = None

    def images(self, candidate, knobs, names):
        if candidate == 'initial':
            return self.baseline
        folder = self.output / 'candidates' / candidate
        folder.mkdir(parents=True, exist_ok=True)
        missing = [name for name in names if not (folder / name).is_file()]
        if missing:
            self.check_budget()
            print(f'照明候補 {candidate} を {len(missing)} 地点で撮影', flush=True)
            request = self.spec | {'knobs': knobs, 'views': [self.views[name] for name in missing],
                                   'save_blend': False}
            render_candidate(request, folder)
            if any(not (folder / name).is_file() for name in missing):
                raise RuntimeError(f'候補 {candidate} の画像が不足しています。')
        return folder

    def panel(self, candidate, image, *, directions=DIRECTIONS):
        from PIL import Image
        from streetlab.roads.walk_panorama import perspective

        suffix='' if directions==DIRECTIONS else '-'+ '-'.join(map(str,directions))
        name = self.panels / f'{candidate}-{image.stem}{suffix}.jpg'
        if not name.is_file():
            with Image.open(image) as panorama:
                panel = Image.new('RGB', (512, 320 * len(directions)))
                for i, yaw in enumerate(directions):
                    panel.paste(perspective(panorama, yaw, width=512, height=320), (0, i * 320))
                buffer = io.BytesIO()
                panel.save(buffer, format='JPEG', quality=90)
            temporary = name.with_suffix('.tmp')
            temporary.write_bytes(buffer.getvalue())
            temporary.replace(name)
        return name

    def visual_distance(self,a,b,knobs):
        if a==b:return 0.
        names=[group[0] for group in self.groups]
        for candidate in (a,b):
            if candidate not in self.appearances:
                folder=self.images(candidate,knobs[candidate],names)
                self.appearances[candidate]=appearance_descriptor([self.panel(candidate,folder/name) for name in names])
        return appearance_distance(self.appearances[a],self.appearances[b])

    def view_judgments(self,pair,name,images,confirm,directions):
        panels={candidate:self.panel(candidate,folder/name,directions=directions) for candidate,folder in images.items()}
        winners=[]
        for order in (pair,pair[::-1]) if confirm else (pair,):
            key=[order,name] if directions==DIRECTIONS else [order,name,'additional-directions-v1',directions]
            path=self.votes/(fingerprint(key)+'.json')
            if path.is_file():record=read(path)
            else:
                self.check_budget()
                response=self.judge(*(panels[candidate] for candidate in order),self.condition)
                label=response['winner']
                record=response|{'image':name,'order':order,'directions':list(directions),
                                  'chosen':{'A':order[0],'B':order[1],'tie':None}[label]}
                save(path,record)
            winners.append(record['chosen'])
        return winners

    def compare(self, a, b, groups, knobs, *, confirm=False):
        if a == b:
            return 0
        names = [name for group in groups for name in group]
        images = {candidate: self.images(candidate, knobs[candidate], names) for candidate in (a, b)}
        totals = []
        for group in groups:
            votes = []
            for name in group:
                pair = sorted([a, b])
                if int(fingerprint([pair, name])[-1], 16) % 2:
                    pair.reverse()
                winners=self.view_judgments(pair,name,images,confirm,DIRECTIONS)
                chosen = winners[0] if len(set(winners)) == 1 else None
                if confirm and chosen is None:
                    sides=self.view_judgments(pair,name,images,True,SIDE_DIRECTIONS)
                    other=sides[0] if len(set(sides))==1 else None
                    # Extra views may resolve abstentions, but cannot override
                    # an explicit contradictory choice in the original views.
                    if other is not None and all(vote is None or vote==other for vote in winners):chosen=other
                votes.append(1 if chosen == a else -1 if chosen == b else 0)
            # Each stretch gets one vote, regardless of its number of photos.
            total = sum(votes)
            totals.append(1 if total > 0 else -1 if total < 0 else 0)
        return sum(totals)

    def tournament(self,participants,incumbent,knobs):
        points=Counter({candidate:0. for candidate in participants})
        matches=[]
        for a,b in combinations(participants,2):
            margin=self.compare(a,b,self.groups,knobs,confirm=True)
            points[a]+=(margin>0)+.5*(margin==0)
            points[b]+=(margin<0)+.5*(margin==0)
            matches.append({'a':a,'b':b,'margin':margin})
        # A tied tournament retains its incumbent instead of rotating through
        # a cycle of pairwise preferences.
        ranked=sorted(participants,key=lambda candidate:(points[candidate],candidate==incumbent),reverse=True)
        return ranked,{'participants':participants,'points':dict(points),'matches':matches}

    def run_round(self, state, seed):
        self.check_budget()
        index = len(state['history'])
        activity=branch_activity(state)
        pending = self.output / f'round-{index + 1:03}.json'
        # A code update must not replace a partially rendered round's proposals.
        candidates = read(pending) if pending.is_file() else proposals(state, index, seed)
        if not pending.is_file():
            save(pending, candidates)
        knobs = state['knobs'] | {row['id']: row['knobs'] for row in candidates}
        print(f'照明探索 {index + 1}周目: {len(candidates)}候補、{len(state["branches"])}枝', flush=True)
        screen = [group[:1] for group in self.groups]
        shortlisted = []
        for row in candidates:
            paired = [self.compare(row['id'], parent, screen, knobs) for parent in row['parents']]
            row['screen_margin'] = min(paired)
            if min(paired) >= 0:
                shortlisted.append(row)
        shortlisted.sort(key=lambda row: (row['screen_margin'],
            knob_distance(row['knobs'], knobs[state['best']])), reverse=True)
        # Give each surviving branch its strongest trial before spending the
        # remaining confirmation slots on alternatives from the same parent.
        leaders = {}
        for row in shortlisted:
            if row['kind'] == 'variation':
                leaders.setdefault(row['parents'][0], row)
        priority = list(leaders.values())
        priority_ids = {row['id'] for row in priority}
        priority.extend(row for row in shortlisted if row['id'] not in priority_ids)

        branches = state['branches'].copy()
        best = state['best']
        grown = set()
        for row in priority[:max(4, len(branches) + 2)]:
            candidate, parents = row['id'], row['parents']
            margins = {parent: self.compare(candidate, parent, self.groups, knobs, confirm=True)
                       for parent in parents}
            row['confirmed_margins'] = margins
            kind = row['kind']
            if min(margins.values()) > 0:
                if kind == 'variation' and parents[0] in branches and parents[0] not in grown:
                    grown.add(parents[0])
                    branches[branches.index(parents[0])] = candidate
                else:
                    branches.append(candidate)
            elif kind == 'broad' and min(margins.values()) >= 0:
                # A distinct region that ties the current best can still improve later.
                branches.append(candidate)

        # Compare active branches on shared views so wins against different parents
        # do not determine the global best. Keep the incumbent available to revisit.
        branches=list(dict.fromkeys(branches))
        margins = {candidate: self.compare(candidate, best, self.groups, knobs, confirm=True)
                   for candidate in branches}
        ranked = sorted(margins, key=margins.get, reverse=True)
        tournament=None
        if (index+1)%TOURNAMENT_INTERVAL==0 and len(branches)>1:
            participants=list(dict.fromkeys([best,*ranked]))[:TOURNAMENT_SIZE]
            leaders,tournament=self.tournament(participants,best,knobs)
            best=leaders[0]
            ranked=[*leaders,*(candidate for candidate in ranked if candidate not in leaders)]
        elif ranked and margins[ranked[0]] > 0:
            best = ranked[0]
        active = [best]
        # Leave at least two trial slots for new combinations each round.
        for candidate in ranked:
            if len(active) >= NEXT_TRIALS - 3:
                break
            if all(self.visual_distance(candidate,other,knobs)>=APPEARANCE_THRESHOLD for other in active):
                active.append(candidate)
        restarted = state.pop('restart_pending', False)
        for parent,data in activity.items():
            data['local_trials']=sum(row['kind']=='variation' and row['parents'][0]==parent for row in candidates)
            data['resting']=data['local_trials']==0
        state['history'].append({'round': index + 1, 'best': best, 'branches': active,
                                 'trials': candidates, 'margins_against_previous_best': margins,
                                 'restart': restarted,
                                 'branch_allocation':activity,'tournament':tournament,
                                 'optimizer': candidates[0].get('optimizer', 'legacy') if candidates else OPTIMIZER_VERSION})
        state.update(knobs=knobs, branches=active, best=best,
                     stall=state['stall'] + 1 if best == state['best'] and not restarted else 0)
        save(self.output / 'state.json', state)
        print(f'照明探索 {index + 1}周目完了: 暫定最良 {best}、{len(active)}枝', flush=True)

    def finish(self, state, stop_reason):
        best, knobs = state['best'], state['knobs']
        finalists = list(dict.fromkeys(['initial', best, *state['branches'], *state.get('retained_finalists', [])]))
        checks = [[name] for name in self.heldout]
        wins = Counter({candidate: 0 for candidate in finalists})
        baseline_margins = {'initial': 0}
        matches = []
        for a, b in combinations(finalists, 2):
            margin = self.compare(a, b, checks, knobs, confirm=True)
            matches.append({'a': a, 'b': b, 'margin': margin})
            wins[a] += (margin > 0) + .5 * (margin == 0)
            wins[b] += (margin < 0) + .5 * (margin == 0)
            if a == 'initial':
                baseline_margins[b] = -margin
        ranked = sorted(finalists, key=lambda c: (wins[c], baseline_margins[c]), reverse=True)
        recommended = next((candidate for candidate in ranked if baseline_margins[candidate] > 0), 'initial')
        ordered = [recommended, *(candidate for candidate in ranked if candidate != recommended)]
        gallery = []
        records = []
        for candidate in ordered:
            source = self.images(candidate, knobs[candidate], self.heldout)
            record = {'id': candidate, 'knobs': knobs[candidate], 'wins': wins[candidate],
                      'margin_against_initial': baseline_margins[candidate], 'images': {}}
            record['blend'] = str(self.export(candidate, knobs[candidate]))
            record['settings'] = str(Path(record['blend']).with_name('settings.json'))
            if 'placement' in self.spec and candidate != 'initial':
                record['placement'] = read(Path(record['settings']))['placement']
            records.append(record)
            cards = []
            for name in self.heldout:
                panel = self.panel(candidate, source / name)
                record['images'][name] = str(source / name)
                cards.append(f'<a href="{link(self.output, source / name)}"><img loading="lazy" '
                             f'src="{link(self.output, panel)}" alt="{escape(name)}"></a>')
            label = '推奨' if candidate == recommended else '比較案'
            name = '初期の照明' if candidate == 'initial' else candidate
            gallery.append(f'<section><h2>{label} · {escape(name)}</h2>'
                           f'<p>初期設定に対する票差 {baseline_margins[candidate]:+d}。'
                           f' <a href="{link(self.output, Path(record["blend"]))}">Blenderを開く</a></p>'
                           f'<div class="views">{"".join(cards)}</div></section>')
        summary = {'stop_reason': stop_reason, 'rounds': len(state['history']), 'condition': self.condition,
                   'search_best': best, 'recommended': recommended, 'heldout_views': self.heldout,
                   'matches': matches, 'finalists': records}
        changed=0;previous='initial'
        for step in state['history']:
            if step['best']!=previous:changed=step['round']
            previous=step['best']
        summary.update(last_best_change_round=changed,rounds_since_best_change=len(state['history'])-changed,
                       trial_count=sum(len(step['trials']) for step in state['history']))
        if self.run_info:
            summary['run'] = self.run_info
        if 'placement' in self.spec:
            summary['inventory'] = self.spec['placement']['inventory']
        save(self.output / 'summary.json', summary)
        run_heading = (f'<p>モデル：{escape(self.run_info["model"])} · '
                       f'{escape(self.run_info["created_at"])}（日本時間）</p>') if self.run_info else ''
        html = ('<!doctype html><html lang="ja"><meta charset="utf-8"><title>照明探索の結果</title>'
                '<style>body{font:16px system-ui;margin:32px}section{margin-block:32px}'
                '.views{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}'
                'img{display:block;width:100%;height:auto}</style><h1>照明探索の結果</h1>'
                + run_heading +
                f'<p>{escape(self.condition)} · {len(state["history"])}周 · {escape(stop_reason)}</p>'
                f'<p>暫定最良案の最終更新：{changed}周目 · 以降{len(state["history"])-changed}周更新なし</p>'
                '<p><a href="summary.json">設定と最終判定</a> · <a href="state.json">枝の履歴</a></p>'
                + ''.join(gallery) + '</html>')
        temporary = self.output / 'index.tmp'
        temporary.write_text(html, encoding='utf-8')
        temporary.replace(self.output / 'index.html')
        print('照明探索の結果:', self.output / 'index.html', flush=True)
        if self.run_info:
            print('過去の実行一覧:', update_history(self.output.parent), flush=True)

    def export(self, candidate, knobs):
        folder = self.output / 'finalists' / candidate
        folder.mkdir(exist_ok=True, parents=True)
        filename = f'{self.run_info["name"]}_{candidate}.blend' if self.run_info else 'scene.blend'
        blend = folder / filename
        if not (folder / 'settings.json').is_file() or not blend.is_file():
            render_candidate(self.spec | {'knobs': knobs, 'save_blend': True,
                                          'blend_filename': filename, 'original_lighting': candidate == 'initial'}, folder)
        if not blend.is_file():
            raise RuntimeError(f'最終案のBlenderを保存できませんでした。{folder}')
        return blend


def link(root, path):
    try:
        return quote(Path(os.path.relpath(path, root)).as_posix())
    except ValueError:  # Different drives on Windows.
        return path.as_uri()


def run_search(folder, *, output=None, resume=None, rounds=None, minutes=None, seed=None, model=None,
               env_file=None, placement='auto', placement_run=None, dry_run=False):
    folder = Path(folder).resolve(strict=True)
    context = capture_context(folder, placement, placement_run)
    spec, _, groups, heldout, condition, signature = context
    layout = spec.get('placement')
    parent = Path(output).resolve() if output else None
    source = Path(resume).resolve(strict=True) if resume else None
    # An old run used as --output only selects its parent directory. Continue
    # history exclusively when --resume is explicit; each new world starts fresh.
    if parent is not None and (parent / 'state.json').is_file():
        parent = parent.parent
    if source is not None and not (source / 'state.json').is_file():
        raise ValueError('--resume には state.json がある探索フォルダを指定してください。')
    if parent is None:
        parent = source.parent if source else folder / (
            'lighting_search_placement' if layout or spec.get('night') else 'lighting_search')
    previous = read(source / 'state.json') if source else None
    minutes = DEFAULT_MINUTES if minutes is None else minutes
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError('探索時間は0より大きい分数を指定してください。')
    if rounds is not None and rounds < 1:
        raise ValueError('照明探索の周回数は1以上を指定してください。')
    api_config = api_settings(env_file or ROOT / '.env')
    use_custom = bool(api_config.get('LIGHTING_API_URL'))
    selected_model = model or (api_config.get('LIGHTING_MODEL') if use_custom else
                              previous['identity']['model'] if previous else None)
    selected_seed = seed if seed is not None else previous['identity']['seed'] if previous else 42
    if dry_run:
        name = selected_model if use_custom else hpc_settings(env_file, selected_model)[2]
        if not name:
            raise ValueError('.env の LIGHTING_MODEL または --model にモデル名を指定してください。')
        output = allocate_run(parent, name, preview=True)
        print(json.dumps({'input': str(folder), 'output': str(output), 'condition': condition,
                          'model': name, 'resume_from': str(source) if source else None,
                          'search_views': groups, 'heldout_views': heldout, 'rounds': rounds, 'minutes': minutes,
                          'placement_anchors': dict(Counter(a['kind'] for a in layout['anchors'])) if layout else {}},
                         ensure_ascii=False, indent=2))
        return output
    with ExitStack() as resources:
        custom = LightingAPI(api_config, selected_model) if use_custom else None
        requested_identity = {'version': 1, 'input': signature, 'seed': selected_seed,
                              'model': custom.model if custom else selected_model, 'groups': groups, 'heldout': heldout}
        if custom:
            requested_identity['judge'] = {'backend': 'lighting-api', 'url': custom.url,
                'revision': custom.revision, 'inputs': 'separate-directions-v1'}
        if custom:
            identity = requested_identity
            judge = custom
            print('照明の審査モデル:', custom.model, custom.revision[:12], flush=True)
        else:
            from openai import OpenAI
            key, endpoint, resolved_model = hpc_settings(env_file, selected_model)
            client = resources.enter_context(OpenAI(api_key=key, base_url=endpoint, max_retries=2, timeout=120,
                                                     default_headers={'User-Agent': 'curl/8.5.0'}))
            identity = requested_identity | {'model': resolved_model}
            judge = create_judge(client, resolved_model)
        if previous and previous['identity'] != identity:
            raise ValueError('再開元の街・撮影条件・審査モデル・乱数と一致しません。別モデルは再開元を指定せず開始してください。')
        output = allocate_run(parent, identity['model'])
        resources.enter_context(run_lock(output))
        run_info = {'name': output.name, 'model': identity['model'],
                    'created_at': datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds'),
                    'build_dir': str(folder), 'resume_from': str(source) if source else None,
                    'minutes':minutes,'round_limit':rounds,'population_protocol':'adaptive-branches-v1'}
        if custom:
            run_info['model_revision'] = custom.revision
        save(output / 'run.json', run_info)
        update_history(parent)
        print('今回の保存先:', output, flush=True)
        state_file = output / 'state.json'
        state = resume_cache(source, output, identity) if source else None
        if state is None:
            initial = baseline_knobs(spec['capture'], layout['anchors'] if layout else ())
            state = {'identity': identity, 'knobs': {'initial': initial},
                     'history': [], 'branches': ['initial'], 'best': 'initial', 'stall': 0}
            if spec.get('night'):
                state['night'] = True
            if layout and layout.get('legacy'):
                state['legacy_placement'] = True
            save(state_file, state)
        from openai import APIError
        try:
            search = Search(output, context, judge, run_info)
            reason = search.explore(state, selected_seed, rounds=rounds, minutes=minutes)
            search.finish(state, reason)
        except APIError as error:
            # API responses can contain private endpoint or request details.
            raise RuntimeError(f'HPCの比較で停止しました ({type(error).__name__})。--resume "{output}" で再開できます。') from None
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_dir', type=Path, help='道路の本撮影が完了した街の保存先')
    parser.add_argument('--output', type=Path, help='実行フォルダを置く親フォルダ。モデル名＋日時の子フォルダを自動作成')
    parser.add_argument('--resume', type=Path, help='同じ世界・同じモデルの探索を明示的に再開。省略時は新規探索')
    parser.add_argument('--rounds', type=int, help='動作確認用の累計周回上限。省略時は時間で終了')
    parser.add_argument('--minutes', type=float, help='探索する分数。省略時は30分。時間後に最終比較・保存')
    parser.add_argument('--seed', type=int, help='候補生成の乱数。既定は42')
    parser.add_argument('--model', help='公開APIに登録したモデル名。既定は.envのLIGHTING_MODEL')
    parser.add_argument('--env-file', type=Path, help='HPC接続設定ファイル')
    parser.add_argument('--placement', choices=('auto', 'on', 'off'), default='auto',
                        help='灯具と窓の配置を探索する。auto は夕方・明け方・夜に使用')
    parser.add_argument('--placement-run', type=Path, help='同じ街の旧配置ループの保存先を使う')
    parser.add_argument('--dry-run', action='store_true', help='入力と使用する撮影地点を確認')
    args = parser.parse_args(argv)
    if args.placement == 'off' and args.placement_run:
        parser.error('--placement off と --placement-run は同時に使えません。')
    try:
        run_search(args.build_dir, output=args.output, resume=args.resume, rounds=args.rounds, minutes=args.minutes, seed=args.seed,
                   model=args.model, env_file=args.env_file, placement=args.placement,
                   placement_run=args.placement_run, dry_run=args.dry_run)
    except (OSError, RuntimeError, ValueError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f'停止: {error}\n')


if __name__ == '__main__':
    main()
