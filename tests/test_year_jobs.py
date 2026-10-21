import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from baseline.year_jobs import status,import_year

class YearJobsTest(unittest.TestCase):
    def test_cache_and_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            with sqlite3.connect(p/'tnic.sqlite') as db:
                db.execute('CREATE TABLE firms(year INTEGER)');db.execute('INSERT INTO firms VALUES(2022)')
                db.execute('CREATE TABLE financial_models(year INTEGER)');db.execute('INSERT INTO financial_models VALUES(2023)')
            self.assertEqual(status(p,2023,True)['state'],'ready')
            self.assertEqual(status(p,2022)['state'],'missing')
            self.assertEqual(status(p,2008,True)['state'],'unsupported')
            self.assertEqual(status(p,2021,True)['state'],'unsupported')

    def test_import_preserves_existing_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for year in (2022,2023):
                for q in range(1,5):
                    with zipfile.ZipFile(p/f'{year}q{q}.zip','w') as z:
                        z.writestr('sub.txt','adsh\tcik\tname\tsic\tfiled\tperiod\tform\n'+f'{year}-{q}\t1\tExample\t3571\t{year}0101\t{year-1}1231\t10-K\n')
                import_year(p,p,year)
            import_year(p,p,2022)
            with sqlite3.connect(p/'tnic.sqlite') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM sic_filings').fetchone()[0],8)
