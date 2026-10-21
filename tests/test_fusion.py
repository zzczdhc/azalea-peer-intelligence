import unittest
from baseline.fusion import fuse_scores,percentiles
class FusionTest(unittest.TestCase):
    def test_average_ties(self):
        self.assertEqual(percentiles({1:0,2:0,3:1}),{1:.25,2:.25,3:1})
    def test_common_pool_and_contributions(self):
        rows,w=fuse_scores({'business':{1:.9,2:.2,3:.8},'financial':{1:-5,2:-1}},{'business':70,'financial':30})
        self.assertEqual([r['gvkey'] for r in rows],[1,2])
        self.assertAlmostEqual(rows[0]['score'],.7)
        self.assertAlmostEqual(sum(rows[0]['contributions'].values()),rows[0]['score'])
    def test_zero_weight_and_invalid(self):
        rows,_=fuse_scores({'business':{1:.1,2:.5}},{'business':1,'risk':0})
        self.assertEqual(rows[0]['gvkey'],2)
        for weights in ({'business':0},{'business':float('nan')},{'business':-1}):
            with self.assertRaises(ValueError):fuse_scores({'business':{1:1}},weights)

    def test_section_builds_are_independent_and_directory_input_works(self):
        import tempfile,sqlite3
        from pathlib import Path
        from baseline.text_similarity import build_text,text_neighbours
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'texts';source.mkdir()
            with sqlite3.connect(root/'tnic.sqlite') as db:
                db.executescript('CREATE TABLE sic_filings(cik INTEGER,adsh TEXT,filed TEXT,form TEXT); CREATE TABLE firms(gvkey INTEGER,year INTEGER); CREATE TABLE identities(gvkey INTEGER,cik INTEGER,name TEXT,ticker TEXT);')
                for g,words in [(1,'memory chip'),(2,'memory chip'),(3,'mining gold')]:
                    db.execute('INSERT INTO sic_filings VALUES (?,?,?,?)',(g,str(g),'2023-02-01','10-K'))
                    db.execute('INSERT INTO firms VALUES (?,2023)',(g,))
                    db.execute('INSERT INTO identities VALUES (?,?,?,?)',(g,g,str(g),'X'))
                    (source/f'{g}.txt').write_text((words+' ')*120)
            build_text(source,root,2023)
            build_text(source,root,2023,'risk')
            with sqlite3.connect(root/'tnic.sqlite') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM text_vectors').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(*) FROM risk_vectors').fetchone()[0],2)
                self.assertEqual(text_neighbours(db,2023,1,section='risk')['peers'][0]['gvkey'],2)
