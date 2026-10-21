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
