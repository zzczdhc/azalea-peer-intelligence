"""On-demand, serialized SEC annual financial builds with persistent source cache."""
import csv
import io
import os
from pathlib import Path
import sqlite3
import threading
import urllib.request
import zipfile
from datetime import date
try:
    from .fundamentals import build_fundamentals
except ImportError:
    from fundamentals import build_fundamentals

_jobs={}
_lock=threading.Lock()
_build_lock=threading.Lock()


def status(data,year,start=False):
    if not 2010 <= year < date.today().year:
        return dict(state='unsupported',message='Automatic financial preparation supports complete years from 2010 onward.')
    data=Path(data).resolve();key=(str(data),year)
    with _lock:
        if key in _jobs and (_jobs[key]['state']=='running' or (_jobs[key]['state']=='error' and not start)):
            return dict(_jobs[key])
        with sqlite3.connect(data/'tnic.sqlite',timeout=30) as db:
            table=db.execute("SELECT 1 FROM sqlite_master WHERE name='financial_models'").fetchone()
            if table and db.execute('SELECT 1 FROM financial_models WHERE year=?',(year,)).fetchone():
                return dict(state='ready',message=f'{year} financial profiles loaded from local cache.')
            if not db.execute('SELECT 1 FROM firms WHERE year=? LIMIT 1',(year,)).fetchone():
                return dict(state='unsupported',message='No research company universe for this year.')
        if not start:return dict(state='missing',message=f'{year} financial data has not been prepared.')
        _jobs[key]=dict(state='running',message=f'Queued: preparing {year} financial data. The first download may take several minutes.')
        threading.Thread(target=_run,args=(data,year,key),daemon=True).start()
        return dict(_jobs[key])


def _update(key,**values):
    with _lock:_jobs[key]=values


def import_year(source,data,year):
    rows=[]
    for quarter in range(1,5):
        with zipfile.ZipFile(source/f'{year}q{quarter}.zip') as z:
            with z.open('sub.txt') as raw:
                for r in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig'),delimiter='\t'):
                    filed=r['filed'];sic=r['sic'].strip()
                    if r['form'] not in ('10-K','10-Q','10-K/A','10-Q/A') or not sic.isdigit() or not 100<=int(sic)<=9999 or len(filed)!=8 or not filed.isdigit():continue
                    rows.append((r['adsh'],int(r['cik']),r['name'],sic.zfill(4),f'{filed[:4]}-{filed[4:6]}-{filed[6:]}',r['period'],r['form'],f'{year}q{quarter}'))
    with sqlite3.connect(data/'tnic.sqlite',timeout=60) as db:
        db.execute('CREATE TABLE IF NOT EXISTS sic_filings(adsh TEXT PRIMARY KEY,cik INTEGER,filing_name TEXT,sic TEXT,filed TEXT,period TEXT,form TEXT,source_quarter TEXT)')
        db.executemany('INSERT OR REPLACE INTO sic_filings VALUES (?,?,?,?,?,?,?,?)',rows)
        db.execute('CREATE INDEX IF NOT EXISTS sic_cik_date ON sic_filings(cik,filed)')


def _run(data,year,key):
    try:
        with _build_lock:
            source=data.parent/'sic-source';source.mkdir(exist_ok=True)
            for q in range(1,5):
                target=source/f'{year}q{q}.zip'
                if target.exists():
                    with zipfile.ZipFile(target) as z:
                        if not {'sub.txt','num.txt'}.issubset(z.namelist()):raise ValueError('Cached SEC archive is missing required files')
                    continue
                _update(key,state='running',message=f'Downloading SEC {year} Q{q} of 4…')
                url=f'https://www.sec.gov/files/dera/data/financial-statement-data-sets/{year}q{q}.zip'
                contact_file=data/'.sec-user-agent'
                agent=os.environ.get('SEC_USER_AGENT') or (contact_file.read_text().strip() if contact_file.exists() else 'AzaleaPeerIntelligence/1.0 local academic research')
                req=urllib.request.Request(url,headers={'User-Agent':agent,'Accept-Encoding':'identity'})
                partial=target.with_suffix('.zip.part')
                try:
                    with urllib.request.urlopen(req,timeout=120) as response,partial.open('wb') as out:
                        total=0
                        while True:
                            chunk=response.read(1024*1024)
                            if not chunk:break
                            out.write(chunk);total+=len(chunk)
                            _update(key,state='running',message=f'Downloading SEC {year} Q{q} of 4: {total//1048576} MB received…')
                    with zipfile.ZipFile(partial) as z:
                        if not {'sub.txt','num.txt'}.issubset(z.namelist()):raise ValueError('Invalid SEC archive')
                    partial.replace(target)
                finally:
                    partial.unlink(missing_ok=True)
            _update(key,state='running',message=f'Linking {year} filings and computing annual financial profiles…')
            import_year(source,data,year)
            build_fundamentals(source,data,year)
            _update(key,state='ready',message=f'{year} financial profiles prepared and cached.')
    except Exception as e:
        _update(key,state='error',message=f'Unable to prepare {year}: {e}. Cached completed quarters are retained. Select the company again to retry. If SEC denies access, configure SEC_USER_AGENT with a real contact or place the quarterly ZIPs in outputs/sic-source.')
