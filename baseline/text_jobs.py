"""Year-specific TF-IDF preparation using a local pre-parsed Business archive."""
import os
from pathlib import Path
import sqlite3
import threading
import time
try:
    from . import year_jobs
    from .text_similarity import build_text
except ImportError:
    import year_jobs
    from text_similarity import build_text

_jobs={}
_lock=threading.Lock()


def source_path(data,section="business"):
    config=data/f'.{section}-text-source'
    value=os.environ.get(section.upper()+'_TEXT_ZIP') or (config.read_text().strip() if config.exists() else '')
    if value:return Path(value).expanduser().resolve()
    root=data.parents[1]/'data'
    folder=root/{'business':'10-K/1','risk':'10-K-Risk/1A','mda':'10-K-MDA/7'}[section]
    return folder if folder.is_dir() else root/{'business':'10-K_item1.zip','risk':'10-K_item1A.zip','mda':'10-K_item7.zip'}[section]


def status(data,year,start=False,section="business"):
    if section not in ("business","risk","mda"):raise ValueError("Invalid section")
    table="text_vectors" if section=="business" else section+"_vectors"
    audit="text" if section=="business" else section
    data=Path(data).resolve();key=(str(data),year) if section=="business" else (str(data),year,section)
    with _lock:
        if key in _jobs and (_jobs[key]['state']=='running' or (_jobs[key]['state']=='error' and not start)):
            return dict(_jobs[key])
        with sqlite3.connect(data/'tnic.sqlite',timeout=30) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if table in tables:
                n=db.execute(f'SELECT count(*) FROM {table} WHERE year=?',(year,)).fetchone()[0]
                if n and (data/f'{audit}_audit_{year}.json').exists():
                    return dict(state='ready',message=f'{year} TF-IDF loaded from cache: {n} companies.',documents=n)
            if not 2010<=year<=2023 or not db.execute('SELECT 1 FROM firms WHERE year=? LIMIT 1',(year,)).fetchone():
                return dict(state='unsupported',message=f'Automatic {section} TF-IDF supports indexed years 2010–2023.')
            if 'identities' not in tables:
                return dict(state='error',message='Company CIK–GVKEY mapping is missing. Follow the baseline setup guide before preparing TF-IDF.')
        source=source_path(data,section)
        if not source.exists():
            return dict(state='missing_source',message=f'{section} text archive is missing. Configure {section.upper()}_TEXT_ZIP or start.py --{section}-text /path/to/archive.zip.')
        if not start:return dict(state='missing',message=f'TF-IDF has not been prepared for {year}.')
        _jobs[key]=dict(state='running',message=f'Preparing {year} {section} TF-IDF…')
        threading.Thread(target=_run,args=(data,year,key,source,section),daemon=True).start()
        return dict(_jobs[key])


def update(key,**values):
    with _lock:_jobs[key]=values


def _run(data,year,key,source,section="business"):
    try:
        # Reuse the SEC annual import, including its download cache and progress.
        job=year_jobs.status(data,year,start=True)
        while job['state']=='running':
            update(key,state='running',message='TF-IDF filing metadata: '+job['message'])
            time.sleep(2);job=year_jobs.status(data,year)
        if job['state']!='ready':raise ValueError(job['message'])
        update(key,state='running',message=f'Queued: fitting {year} Business vocabulary and IDF…')
        with year_jobs._build_lock:
            update(key,state='running',message=f'Computing {year} {section} TF-IDF vectors and source checksum…')
            report=build_text(source,data,year) if section=="business" else build_text(source,data,year,section)
        update(key,state='ready',message=f'{year} TF-IDF prepared: {report["documents"]} companies.',documents=report['documents'])
    except Exception as e:
        update(key,state='error',message=f'Unable to prepare {year} TF-IDF: {e}. Select the company again to retry. Existing years are preserved.')
