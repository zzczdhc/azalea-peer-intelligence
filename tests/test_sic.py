import sqlite3
import unittest
from baseline.sic import sic_peers

class SIC(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''
        CREATE TABLE firms(year INTEGER,gvkey INTEGER,peer_count INTEGER);
        CREATE TABLE peers(year INTEGER,gvkey INTEGER,rank INTEGER,peer INTEGER,score REAL);
        CREATE TABLE identities(gvkey INTEGER,name TEXT,ticker TEXT,cik TEXT);
        CREATE TABLE sic_filings(adsh TEXT,cik INTEGER,filing_name TEXT,sic TEXT,filed TEXT,period TEXT,form TEXT,source_quarter TEXT);
        ''')
        self.db.executemany('INSERT INTO firms VALUES (2023,?,1)',[(1,),(2,),(3,),(4,),(5,),(6,)])
        self.db.executemany('INSERT INTO identities VALUES (?,?,?,?)',[(1,'MICRON','MU','10'),(2,'CHIP CO','CHIP','20'),(3,'CONNECTOR CO','CON','30'),(4,'OLD CO','OLD','40'),(5,'DUPLICATE A','DA','50'),(6,'DUPLICATE B','DB','50')])
        self.db.execute('INSERT INTO peers VALUES (2023,1,1,2,0.1)')
        rows=[('a',10,'MICRON','3674','2023-10-01','20230831','10-K','2023q4'),
              ('b',20,'CHIP CO','3674','2023-11-01','20230930','10-Q','2023q4'),
              ('c',30,'CONNECTOR CO','3678','2023-09-01','20230630','10-Q','2023q3'),
              ('d',40,'OLD CO','3674','2024-01-01','20231231','10-K','2024q1'),
              ('e',50,'DUPLICATE','3674','2023-09-01','20230630','10-Q','2023q3')]
        self.db.executemany('INSERT INTO sic_filings VALUES (?,?,?,?,?,?,?,?)',rows)

    def test_prefix_self_and_future_exclusion(self):
        p=sic_peers(self.db,2023,1,3)
        self.assertEqual([c['gvkey'] for c in p['peers']],[2,3])
        self.assertEqual(p['sic_covered'],3)
        self.assertEqual(p['overlap'],1)
        self.assertEqual(p['group_counts'],{'2':2,'3':2})
        self.assertEqual(p['as_of'],'2023-12-31')
        self.assertNotIn(1,[r['gvkey'] for r in p['peers']])
        self.assertIn('/10/a/a-index.htm',p['company']['source_url'])

    def test_membership_is_not_ranked_and_intersection_is_complete(self):
        p=sic_peers(self.db,2023,1,3,limit=1)
        self.assertEqual(p['total_peers'],2)
        self.assertEqual(p['peers'][0]['name'],'CHIP CO')
        self.assertNotIn('score',p['peers'][0])
        # Intersection is independent of the current alphabetic page.
        page=sic_peers(self.db,2023,1,3,offset=1,limit=1)
        self.assertEqual([c['gvkey'] for c in page['overlap_peers']],[2])

    def test_latest_observation_and_pagination(self):
        self.db.execute("INSERT INTO sic_filings VALUES ('z',20,'CHIP CO','3559','2023-12-01','20230930','10-Q','2023q4')")
        p=sic_peers(self.db,2023,1,3)
        self.assertEqual([c['gvkey'] for c in p['peers']],[3])
        self.assertEqual(sic_peers(self.db,2023,1,3,offset=1)['peers'],[])

    def test_missing_year_or_company_is_explicit(self):
        self.assertFalse(sic_peers(self.db,2022,1)['available'])
        self.assertFalse(sic_peers(self.db,2023,4)['available'])
        self.assertFalse(sic_peers(self.db,2023,5)['available'])

    def test_invalid_parameters(self):
        with self.assertRaises(ValueError):sic_peers(self.db,2023,1,1)
        with self.assertRaises(ValueError):sic_peers(self.db,2023,1,4)
        with self.assertRaises(ValueError):sic_peers(self.db,2023,1,3,-1)
