from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from baseline.serve import Handler
from baseline.tnic import build

class TNIC(unittest.TestCase):
    def fixture(self, rows):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);p=Path(t.name)
        with zipfile.ZipFile(p/'a.zip','w') as z:
            z.writestr('tnic3_data.txt','year\tgvkey1\tgvkey2\tscore\n'+rows)
        return build(p/'a.zip',p/'out',2),p/'out'
    def test_ranking_and_exclusion(self):
        a,p=self.fixture('2023\t1\t1\t0.9\n2023\t1\t3\t0.2\n2023\t1\t2\t0.2\n2023\t2\t1\t0.2\n2023\t3\t1\t0.2\n2023\t4\t5\t0\n2023\t5\t4\t0\n')
        with sqlite3.connect(p/'tnic.sqlite') as d:
            self.assertEqual(d.execute('SELECT peer FROM peers WHERE gvkey=1 ORDER BY rank').fetchall(),[(2,),(3,)])
            self.assertEqual(d.execute('SELECT peer,score FROM peers WHERE gvkey=4').fetchall(),[(5,0)])
            self.assertEqual(d.execute('SELECT count(*) FROM peers WHERE gvkey=peer').fetchone()[0],0)
        self.assertEqual(a['yearly'][0]['asymmetric_rows'],0)
        self.assertEqual(a['yearly'][0]['self_rows'],1)
    def test_missing_score(self):
        a,p=self.fixture('2023\t1\t2\t\n2023\t2\t1\t\n')
        self.assertEqual(a['yearly'][0]['missing_score_rows'],2)
        with sqlite3.connect(p/'tnic.sqlite') as d:
            self.assertEqual(d.execute('SELECT count(*) FROM peers').fetchone()[0],0)
            self.assertEqual(d.execute('SELECT peer_count FROM firms WHERE gvkey=1').fetchone()[0],1)
    def test_duplicate(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            self.fixture('2023\t1\t2\t0.1\n2023\t1\t2\t0.1\n')
    def test_unordered_years(self):
        a,p=self.fixture('2023\t1\t2\t0.1\n2022\t1\t2\t0.1\n')
        self.assertEqual([y['year'] for y in a['yearly']],[2022,2023])
    def test_invalid(self):
        with self.assertRaisesRegex(ValueError,'Invalid score'):
            self.fixture('2023\t1\t2\tnan\n')


class Explorer(unittest.TestCase):
    def test_query_api(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)
            with zipfile.ZipFile(p/'a.zip','w') as z:
                z.writestr('tnic3_data.txt','year\tgvkey1\tgvkey2\tscore\n2023\t1\t2\t0.25\n2023\t2\t1\t0.25\n')
            build(p/'a.zip',p/'out')
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,data=p/'out'))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base='http://127.0.0.1:'+str(server.server_port)
                with urlopen(base+'/api/peers?year=2023&gvkey=1') as r:
                    self.assertEqual(json.load(r)['peers'],[[1,2,0.25]])
                with urlopen(base+'/api/peers?year=2023&gvkey=999') as r:
                    self.assertFalse(json.load(r)['found'])
                with urlopen(base+'/') as r:
                    self.assertIn('TNIC',r.read().decode())
                with sqlite3.connect(p/'out'/'tnic.sqlite') as d:
                    d.execute('CREATE TABLE identities(gvkey INTEGER PRIMARY KEY,name TEXT,cik TEXT,ticker TEXT,ticker_last_seen TEXT,provider TEXT,status TEXT)')
                    d.executemany('INSERT INTO identities VALUES (?,?,?,?,?,?,?)',[(1,'FIRST COMPANY','0000000010','FIRST','2025-01-01','test','display_only_static_snapshot'),(2,'SECOND COMPANY','0000000020','SECOND','2025-01-01','test','display_only_static_snapshot')])
                with urlopen(base+'/api/search?year=2023&q=FIRST') as r:
                    self.assertEqual(json.load(r)[0]['name'],'FIRST COMPANY')
                with urlopen(base+'/api/search?year=2022&q=FIRST') as r:
                    self.assertEqual(json.load(r),[])
                with urlopen(base+'/api/search?year=2023&q=%25') as r:
                    self.assertEqual(json.load(r),[])
                with urlopen(base+'/api/peers?year=2023&gvkey=1') as r:
                    payload=json.load(r)
                    self.assertEqual(payload['company']['name'],'FIRST COMPANY')
                    self.assertEqual(payload['peer_labels']['2']['ticker'],'SECOND')

            finally:
                server.shutdown();server.server_close();thread.join()

class IdentityMapping(unittest.TestCase):
    def test_prefers_company_source(self):
        from baseline.names import resolve
        preferred=(1004,1750,'AAR CORP','Compustat Company')
        self.assertEqual(resolve([(1004,9999,'OTHER','Capital IQ'),preferred]),preferred)
    def test_conflicting_entities_unresolved(self):
        from baseline.names import resolve
        self.assertIsNone(resolve([(1,10,'FIRST CO','Compustat Company'),(1,20,'OTHER CO','Compustat Company')]))
    def test_punctuation_equivalent(self):
        from baseline.names import resolve
        self.assertIsNotNone(resolve([(1,10,'FIRST CO.','Capital IQ'),(1,10,'FIRST CO','Capital IQ')]))
