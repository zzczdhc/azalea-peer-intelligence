import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from baseline import text_jobs

class TextJobsTest(unittest.TestCase):
    def test_status_distinguishes_missing_source_and_cached_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)
            with sqlite3.connect(data/'tnic.sqlite') as db:
                db.execute('CREATE TABLE firms(year INTEGER)');db.execute('INSERT INTO firms VALUES(2022)')
                db.execute('CREATE TABLE identities(gvkey INTEGER)')
                db.execute('CREATE TABLE text_vectors(year INTEGER)')
            with patch.dict('os.environ',{'BUSINESS_TEXT_ZIP':str(data/'absent.zip')}):
                self.assertEqual(text_jobs.status(data,2022,True)['state'],'missing_source')
            (data/'text_audit_2022.json').write_text('{}')
            with sqlite3.connect(data/'tnic.sqlite') as db:db.execute('INSERT INTO text_vectors VALUES(2022)')
            self.assertEqual(text_jobs.status(data,2022,True)['state'],'ready')

    def test_worker_passes_selected_year_and_preserves_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);key=(tmp,2022)
            with patch.object(text_jobs.year_jobs,'status',return_value={'state':'ready'}),patch.object(text_jobs,'build_text',return_value={'documents':15}) as build:
                text_jobs._run(data,2022,key,data/'text.zip')
                build.assert_called_once_with(data/'text.zip',data,2022)
                self.assertEqual(text_jobs._jobs[key]['state'],'ready')
            with patch.object(text_jobs.year_jobs,'status',return_value={'state':'error','message':'SEC denied'}):
                text_jobs._run(data,2022,key,data/'text.zip')
                self.assertEqual(text_jobs._jobs[key]['state'],'error')
                self.assertIn('SEC denied',text_jobs._jobs[key]['message'])

    def test_duplicate_requests_share_a_single_year_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp);source=data/'text.zip';source.write_bytes(b'placeholder')
            with sqlite3.connect(data/'tnic.sqlite') as db:
                db.execute('CREATE TABLE firms(year INTEGER)');db.execute('INSERT INTO firms VALUES(2022)')
                db.execute('CREATE TABLE identities(gvkey INTEGER)')
            with patch.dict('os.environ',{'BUSINESS_TEXT_ZIP':str(source)}),patch.object(text_jobs.threading,'Thread') as worker:
                self.assertEqual(text_jobs.status(data,2022,True)['state'],'running')
                self.assertEqual(text_jobs.status(data,2022,True)['state'],'running')
                worker.assert_called_once()
                worker.return_value.start.assert_called_once()
