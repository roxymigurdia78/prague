"""Distribute trials among branches and measure appearance on shared views."""
import math

import numpy as np
from PIL import Image


def branch_activity(state):
    history=state['history']
    by_id={row['id']:(step['round'],row) for step in history for row in step['trials']}
    now=len(history)
    result={}
    for branch in state['branches']:
        depths={branch:0};pending=[branch]
        while pending:
            item=pending.pop()
            depth=depths[item]
            if depth>=6:continue
            for parent in by_id.get(item,(0,{}))[1].get('parents',[]):
                if parent=='initial' and branch!='initial':continue
                if parent not in depths or depths[parent]>depth+1:
                    depths[parent]=depth+1;pending.append(parent)
        wins=observed=0.
        last_gain=by_id.get(branch,(0,{}))[0]
        last_trial=last_gain
        for step in history[-8:]:
            for row in step['trials']:
                parents=[parent for parent in row['parents'] if parent in depths]
                if not parents:continue
                parent=min(parents,key=depths.get)
                margins=row.get('confirmed_margins',{})
                weight=.6**depths[parent] * .85**(now-step['round'])
                if parent in margins:margin=margins[parent]
                elif 'screen_margin' in row:
                    margin=row['screen_margin'];weight*=.25
                else:continue
                observed+=weight
                if margin>0:
                    wins+=weight;last_gain=max(last_gain,step['round'])
                if branch in row['parents']:last_trial=max(last_trial,step['round'])
        idle=max(0,now-last_trial)
        priority=((wins+1)/(observed+3)+.5/math.sqrt(observed+1)) * math.exp(-.2*min(8,now-last_gain))
        # Idle branches retain an opportunity to return; their settings also
        # remain in state.knobs when they receive no local trials this round.
        priority=max(.08,priority)+.1*min(idle,4)
        result[branch]={'priority':round(priority,6),'observations':round(observed,3),
                        'wins':round(wins,3),'idle_rounds':idle,'last_gain_round':last_gain}
    return result


def appearance_descriptor(paths):
    values=[]
    for path in paths:
        with Image.open(path) as image:
            values.append(np.asarray(image.convert('RGB').resize((16,20),Image.Resampling.BOX),
                                     dtype=np.float32).ravel()/255)
    if not values:raise ValueError('照明案の見た目を比べる共通画像がありません。')
    return np.concatenate(values)


def appearance_distance(left,right):
    if left.shape!=right.shape:raise ValueError('見た目の比較には同じ視点が必要です。')
    return float(np.sqrt(np.mean((left-right)**2)))
