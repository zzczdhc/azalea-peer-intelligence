from collections import Counter
import json
import math
import sqlite3
import unittest
from baseline.text_similarity import counts,tfidf,cosine,score_peers

class TextSimilarity(unittest.TestCase):
    def test_formula_normalization_and_cosine(self):
        c=Counter({'memory':4,'chip':1,'unique':1,'common':2})
        df={'memory':2,'chip':2,'unique':1,'common':3}
        v=tfidf(c,df,3)
        self.assertEqual(set(v),{'memory','chip'})
        self.assertAlmostEqual(v['memory']/v['chip'],1+math.log(4))
        self.assertAlmostEqual(sum(x*x for x in v.values()),1)
        self.assertAlmostEqual(cosine(v,v),1)
        self.assertEqual(cosine({'a':1},{'b':1}),0)

    def test_tokenizer_stopwords(self):
        self.assertEqual(counts('The COMPANY makes memory memory.'),Counter({'makes':1,'memory':2}))

    def test_missing_text_is_not_zero_and_no_table_page_filter(self):
        with sqlite3.connect(':memory:') as d:
            d.executescript('''CREATE TABLE text_vectors(year INTEGER,gvkey INTEGER,vector TEXT,adsh TEXT,filed TEXT);
              CREATE TABLE peers(year INTEGER,gvkey INTEGER,rank INTEGER,peer INTEGER);
              CREATE TABLE identities(gvkey INTEGER,name TEXT,ticker TEXT);''')
            d.executemany('INSERT INTO text_vectors VALUES (2023,?,?,?,?)',[(1,json.dumps({'0':1}),'a','2023-01-01'),(2,json.dumps({'0':1}),'b','2023-02-01'),(3,json.dumps({'1':1}),'c','2023-03-01')])
            d.execute('INSERT INTO peers VALUES (2023,1,1,2)')
            group=[dict(gvkey=i,name='Company '+str(i),ticker='X') for i in (2,3,4)]
            p=score_peers(d,2023,1,group)
            self.assertEqual(len(p['companies']),3)
            self.assertEqual([c['score'] for c in p['companies']],[1,0,None])
            self.assertEqual(p['groups']['sic'],dict(total=3,scored=2,mean=.5))
            self.assertEqual(p['groups']['tnic']['mean'],1)
            self.assertFalse(score_peers(d,2022,1,group)['available'])

    def test_text_neighbours_rank_full_corpus_and_exclude_self(self):
        from baseline.text_similarity import text_neighbours
        with sqlite3.connect(':memory:') as d:
            d.executescript('''CREATE TABLE text_vectors(year INTEGER,gvkey INTEGER,vector TEXT,adsh TEXT,filed TEXT);
              CREATE TABLE identities(gvkey INTEGER,name TEXT,ticker TEXT);''')
            for g,vector in [(1,{'0':1}),(2,{'0':1}),(3,{'1':1})]:
                d.execute('INSERT INTO text_vectors VALUES (2023,?,?,?,?)',(g,json.dumps(vector),str(g),'2023-01-01'))
                d.execute('INSERT INTO identities VALUES (?,?,?)',(g,'Company '+str(g),'X'))
            p=text_neighbours(d,2023,1)
            self.assertEqual([c['gvkey'] for c in p['peers']],[2,3])
            self.assertEqual(p['peers'][0]['score'],1)
            self.assertEqual(p['total_candidates'],2)
