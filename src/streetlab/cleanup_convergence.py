"""Persistent, bounded stopping rules for the automatic cleanup feedback loop."""
import hashlib
import json
import math
from pathlib import Path
import re

from streetlab.paths import ROOT

DEFAULTS={'max_reviews':6,'stable_rounds':2,'max_new_protected_fraction':.0001,
          'max_face_change_fraction':.001}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,data):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def load_policy():
    path=ROOT/'config/cleanup_policy.json'
    policy=DEFAULTS| (read(path) if path.exists() else {})
    for key in ('max_reviews','stable_rounds'):
        if type(policy[key]) is not int or policy[key]<1:
            raise ValueError('清掃設定は正の整数にしてください: '+key)
    for key in ('max_new_protected_fraction','max_face_change_fraction'):
        value=policy[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
            raise ValueError('清掃設定は0〜1の割合にしてください: '+key)
    return policy


def geometry(folder):
    folder=Path(folder);asset=folder/'street_without_sky.glb';report=folder/'report.json'
    data=read(report) if report.exists() else {}
    return {'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),
        'kept_faces':data.get('kept_faces'),'total_faces':data.get('total_faces')}


def legacy_reviews(source):
    log=Path(source).resolve().parent.parent/'build_connected.log'
    if not log.exists():return 0
    values=re.findall(r'HPCによる清掃確認ループ (\d+)',log.read_text(encoding='utf-8',errors='replace'))
    return max(map(int,values),default=0)


class Progress:
    def __init__(self,folder,source,policy):
        self.folder=Path(folder);self.path=self.folder/'cleanup_progress.json';self.policy=policy
        if self.path.exists():
            self.data=read(self.path)
        else:
            # The former loop only logged its counter. Carry that work forward
            # when upgrading an existing build rather than granting a new budget.
            count=legacy_reviews(source)
            self.data={'schema':'streetlab/cleanup-progress/v1','reviews_completed':count,
                'legacy_rounds':count,'stable_count':0,'unchanged_count':0,'history':[]}
        if type(self.data.get('reviews_completed')) is not int or self.data['reviews_completed']<0:
            raise ValueError('清掃の確認回数記録が不正です: '+str(self.path))
        self.data['policy']=policy
        self.persist()

    @property
    def count(self):return self.data['reviews_completed']

    @property
    def exhausted(self):return self.count>=self.policy['max_reviews']

    def persist(self):save(self.path,self.data)

    def record_review(self,phase,added,after_folder):
        metrics_file=self.folder/'review_metrics.json'
        metrics=read(metrics_file) if metrics_file.exists() else {}
        valid=(metrics.get('new_regions')==added and metrics.get('reviewed_pixels',0)>0
            and metrics.get('after_folder')==str(Path(after_folder).resolve()))
        fraction=metrics.get('new_protected_fraction') if valid else None
        if fraction is not None and (not isinstance(fraction,(int,float)) or not math.isfinite(fraction)):
            fraction=None
        self.data['reviews_completed']+=1
        entry={'review':self.count,'phase':phase,'added_regions':added,
            'new_protected_pixels':metrics.get('new_protected_pixels') if valid else None,
            'new_protected_fraction':fraction}
        self.data['history'].append(entry);self.data['status']='running';self.persist()
        area=(str(entry['new_protected_pixels'])+'画素 / '+format(fraction*100,'.6f')+'%') if fraction is not None else '面積情報なし'
        print(f'清掃確認 {self.count}/{self.policy["max_reviews"]} ({phase}): 新規保護 {added}件、{area}',flush=True)

    def record_rebuild(self,before,after):
        entry=self.data['history'][-1]
        total=after.get('total_faces');old=before.get('kept_faces');new=after.get('kept_faces')
        fraction=abs(new-old)/total if all(type(v) is int for v in (total,old,new)) and total>0 else None
        unchanged=before['sha256']==after['sha256']
        pixels=entry.get('new_protected_fraction')
        known=pixels is not None and fraction is not None
        small=(known and pixels<=self.policy['max_new_protected_fraction']
            and fraction<=self.policy['max_face_change_fraction'])
        self.data['stable_count']=self.data['stable_count']+1 if small else 0
        self.data['unchanged_count']=self.data['unchanged_count']+1 if unchanged and known else 0
        entry.update(face_change_fraction=fraction,geometry_unchanged=unchanged,
            stable_count=self.data['stable_count'],unchanged_count=self.data['unchanged_count'])
        self.persist()
        change=format(fraction*100,'.6f')+'%' if fraction is not None else '不明'
        print(f'保護を反映: 形状差 {change}、小さい変化 {self.data["stable_count"]}/{self.policy["stable_rounds"]}回連続',flush=True)
        if self.data['unchanged_count']>=self.policy['stable_rounds']:return 'unchanged_geometry'
        if self.data['stable_count']>=self.policy['stable_rounds']:return 'converged'
        return None

    def finish(self,reason):
        self.data.update(status='complete',termination_reason=reason)
        self.persist()


REASONS={'no_new_protection':'仕上げ確認で追加の修復なし',
         'converged':'小さい変化が連続したため収束',
         'unchanged_geometry':'形状が続けて変わらないため打ち切り',
         'max_reviews':'確認回数の上限に到達'}
