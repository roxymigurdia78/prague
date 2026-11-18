from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from streetlab.lighting_population import branch_activity,appearance_descriptor,appearance_distance
from streetlab.lighting_search import Search,proposals,baseline_knobs,DIRECTIONS,SIDE_DIRECTIONS
from streetlab.build_world import read


class PopulationTests(unittest.TestCase):
    def state(self):
        base=baseline_knobs({'white_balance_kelvin':None,'look':'None'})
        state={'knobs':{'initial':base,'hot':base|{'exposure_delta':.5},'cold':base|{'exposure_delta':-.5}},
               'best':'hot','branches':['hot','cold'],'history':[],'stall':0}
        for index in range(1,9):
            rows=[]
            for parent,margin in (('hot',1),('cold',-1)):
                key=f'{parent}-{index}'
                knobs=state['knobs'][parent]|{'tint_delta':index*.1}
                state['knobs'][key]=knobs
                rows.append({'id':key,'knobs':knobs,'parents':[parent],'kind':'variation',
                             'confirmed_margins':{parent:margin}})
            state['history'].append({'round':index,'trials':rows,'best':'hot','branches':['hot','cold']})
        return state

    def test_successful_branches_receive_more_local_trials_and_other_branches_stay_available(self):
        state=self.state();activity=branch_activity(state)
        self.assertGreater(activity['hot']['priority'],activity['cold']['priority'])
        counts={'hot':0,'cold':0}
        for seed in range(20):
            rows=proposals(state,8,seed)
            self.assertEqual(len(rows),12)
            self.assertEqual(sum(row['kind']=='broad' for row in rows),2)
            for row in rows:
                if row['kind']=='variation':counts[row['parents'][0]]+=1
        self.assertGreater(counts['hot'],counts['cold']*2)
        self.assertEqual(state['branches'],['hot','cold'])

    def test_similarity_uses_actual_appearance_not_parameter_values(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for name,color in (('a',150),('b',151),('c',70)):
                Image.new('RGB',(512,640),(color,color,color)).save(folder/(name+'.png'))
            a,b,c=(appearance_descriptor([folder/(name+'.png')]) for name in ('a','b','c'))
            self.assertLess(appearance_distance(a,b),.015)
            self.assertGreater(appearance_distance(a,c),.015)


class ComparisonTests(unittest.TestCase):
    def search(self,folder,judge):
        return Search(folder,({'views':[]},folder,[['view.png']],['heldout.png'],'昼','signature'),judge)

    def test_side_views_resolve_abstentions_and_are_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);calls=[]
            def judge(left,right,condition):
                calls.append((left,right))
                if 'side' not in left.name:return {'winner':'tie','reason':'front/back abstain'}
                return {'winner':'A' if left.name.startswith('a-') else 'B','reason':'side views favor a'}
            search=self.search(folder,judge)
            def panel(candidate,image,*,directions=DIRECTIONS):
                return folder/(candidate+('-side.jpg' if directions==SIDE_DIRECTIONS else '-front.jpg'))
            with patch.object(search,'images',return_value=folder),patch.object(search,'panel',side_effect=panel):
                self.assertEqual(search.compare('a','b',search.groups,{'a':{},'b':{}},confirm=True),1)
                self.assertEqual(len(calls),4)
                search.compare('a','b',search.groups,{'a':{},'b':{}},confirm=True)
                self.assertEqual(len(calls),4)

    def test_side_views_do_not_override_an_explicit_front_view_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            def judge(left,right,condition):
                choice='A' if 'front' in left.name or left.name.startswith('a-') else 'B'
                return {'winner':choice,'reason':'conflicting evidence'}
            search=self.search(folder,judge)
            with patch.object(search,'images',return_value=folder),patch.object(search,'panel',side_effect=
                    lambda candidate,image,*,directions=DIRECTIONS:folder/(candidate+('-front.jpg' if directions==DIRECTIONS else '-side.jpg'))):
                self.assertEqual(search.compare('a','b',search.groups,{'a':{},'b':{}},confirm=True),0)

    def test_periodic_tournament_retains_incumbent_when_preferences_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            search=self.search(Path(directory),None)
            wins={('a','b'),('b','c'),('c','a')}
            def compare(a,b,groups,knobs,*,confirm):
                self.assertEqual(groups,search.groups)
                self.assertTrue(confirm)
                return 1 if (a,b) in wins else -1
            with patch.object(search,'compare',side_effect=compare) as comparisons:
                ranked,result=search.tournament(['a','b','c'],'a',{})
            self.assertEqual(ranked[0],'a')
            self.assertEqual(comparisons.call_count,3)
            self.assertEqual(set(result['points'].values()),{1.})

    def test_third_round_runs_the_tournament_and_records_branch_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);search=self.search(folder,None)
            base=baseline_knobs({'white_balance_kelvin':None,'look':'None'})
            state={'best':'initial','branches':['initial'],'knobs':{'initial':base},'stall':0,
                   'history':[{'round':index,'best':'initial','branches':['initial'],'trials':[]} for index in (1,2)]}
            rows=[{'id':key,'knobs':base|{'exposure_delta':value},'parents':['initial'],'kind':'broad'}
                  for key,value in (('a',.5),('b',1.))]
            def compare(a,b,*args,**kwargs):return 0 if a==b else 1
            report={'participants':['initial','a','b'],'points':{'b':2,'a':1,'initial':0}}
            with patch('streetlab.lighting_search.proposals',return_value=rows),patch.object(
                    search,'compare',side_effect=compare),patch.object(search,'visual_distance',side_effect=
                    lambda a,b,*args:0 if a==b else .5),patch.object(search,'tournament',return_value=(['b','a','initial'],report)) as tournament:
                search.run_round(state,42)
            tournament.assert_called_once()
            saved=read(folder/'state.json')
            self.assertEqual(saved['best'],'b')
            self.assertEqual(saved['history'][-1]['tournament'],report)
            self.assertTrue(saved['history'][-1]['branch_allocation']['initial']['resting'])


if __name__=='__main__':unittest.main()
