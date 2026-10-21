import unittest
from baseline.overlap import partition_sets

class Overlap(unittest.TestCase):
    def test_four_method_regions_are_exact_disjoint_and_complete(self):
        sets=dict(tnic={1,2,3},sic={2,3,4},financial={3,4,5},tfidf={3,5,6})
        r=partition_sets(sets)
        self.assertEqual(len(r),15)
        self.assertEqual(sum(x['count'] for x in r),6)
        self.assertEqual(next(x['gvkeys'] for x in r if len(x['members'])==4),[3])
        self.assertEqual(next(x['gvkeys'] for x in r if x['members']==['sic','financial']),[4])
        all_ids=[g for row in r for g in row['gvkeys']]
        self.assertEqual(len(all_ids),len(set(all_ids)))

    def test_empty_and_contained_sets(self):
        r=partition_sets(dict(a=set(),b={1}))
        self.assertEqual(sum(x['count'] for x in r),1)
        self.assertEqual(next(x['count'] for x in r if len(x['members'])==2),0)
        r=partition_sets(dict(a={1},b={1,2},c={1,2,3}))
        self.assertEqual(next(x['gvkeys'] for x in r if len(x['members'])==3),[1])

    def test_fusion_uses_requested_weights_and_section_sets(self):
        import sqlite3
        from unittest.mock import patch
        from baseline.overlap import overlap_result
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE identities(gvkey INTEGER,name TEXT,ticker TEXT)')
            weights=dict(business=70,risk=0,mda=0,financial=30)
            with patch('baseline.overlap.text_neighbours',return_value=dict(available=True,peers=[dict(gvkey=2)])) as text,patch('baseline.overlap.fusion_result',return_value=dict(available=True,peers=[dict(gvkey=2),dict(gvkey=3)],weights=dict(business=.7,financial=.3))) as fusion:
                result=overlap_result(db,2023,1,3,['risk','mda','fusion'],weights=weights)
                fusion.assert_called_once_with(db,2023,1,weights)
                self.assertEqual([c.kwargs['section'] for c in text.call_args_list],['risk','mda'])
                self.assertEqual(result['intersection_count'],1)
                self.assertEqual(result['fusion_weights'],weights)
