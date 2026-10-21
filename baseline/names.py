"""Attach conservative, display-only company labels to the TNIC index.

Inputs are the public Std_Security_Code Parquet snapshots, not a verified
point-in-time identity master. This does not change the peer selection.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

SOURCE = 'https://github.com/Wenzhi-Ding/Std_Security_Code'


def resolve(candidates):
    """Prefer Compustat Company labels; never select arbitrary conflicting entities."""
    preferred = [r for r in candidates if r[3] == 'Compustat Company'] or candidates
    identities = {(int(r[1]), re.sub(r'[^A-Z0-9]', '', r[2].upper())) for r in preferred}
    if len(identities) != 1:
        return None
    return sorted(preferred, key=lambda r: (r[3], r[2]))[0]


def attach(source, data, runtime=None):
    if runtime:
        sys.path.insert(0, str(runtime))
    import duckdb
    source, data = Path(source), Path(data)
    connection = duckdb.connect()
    links = connection.execute('''SELECT DISTINCT CAST(gvkey AS BIGINT),cik,
                   link_company_name,source FROM read_parquet(?)
                   WHERE gvkey IS NOT NULL AND cik IS NOT NULL
                   AND link_desc='Valid CIK-GVKEY Link'
                   AND link_company_name IS NOT NULL AND trim(link_company_name)<>'' ''',
                   [str(source/'cik_gvkey.pq')]).fetchall()
    tickers = connection.execute('''SELECT CAST(CIK AS BIGINT),Ticker,CAST(LTDate AS DATE)
                   FROM read_parquet(?) WHERE Ticker IS NOT NULL AND LTDate IS NOT NULL''',
                   [str(source/'cik_ticker.pq')]).fetchall()
    by_firm, by_cik = defaultdict(list), defaultdict(list)
    for row in links:
        by_firm[row[0]].append(row)
    for cik, ticker, date in tickers:
        if ticker.strip():
            by_cik[cik].append((date.isoformat(), ticker.strip()))
    rows, ambiguous = [], []
    for gvkey, candidates in by_firm.items():
        chosen = resolve(candidates)
        if chosen is None:
            ambiguous.append(gvkey)
            continue
        _, cik, name, provider = chosen
        ts = by_cik.get(cik, [])
        last = max((v[0] for v in ts), default=None)
        symbols = sorted({v[1] for v in ts if v[0] == last})
        # Multiple securities are exposed together, never silently treated as one.
        rows.append((gvkey, name, str(cik).zfill(10), ' / '.join(symbols), last,
                     provider, 'display_only_static_snapshot'))
    with sqlite3.connect(data/'tnic.sqlite') as db:
        db.execute('DROP TABLE IF EXISTS identities')
        db.execute('''CREATE TABLE identities(gvkey INTEGER PRIMARY KEY,name TEXT,
                   cik TEXT,ticker TEXT,ticker_last_seen TEXT,provider TEXT,status TEXT)''')
        db.executemany('INSERT INTO identities VALUES (?,?,?,?,?,?,?)',rows)
        db.execute('CREATE INDEX identities_name ON identities(name COLLATE NOCASE)')
        db.execute('CREATE INDEX identities_ticker ON identities(ticker COLLATE NOCASE)')
        coverage = [dict(year=y,total=n,mapped=m) for y,n,m in db.execute('''
            SELECT f.year,count(*),count(i.gvkey) FROM firms f
            LEFT JOIN identities i ON i.gvkey=f.gvkey GROUP BY f.year ORDER BY f.year''')]
        matched = db.execute('SELECT count(DISTINCT i.gvkey) FROM identities i JOIN firms f USING(gvkey)').fetchone()[0]
    hashes = {f: hashlib.sha256((source/f).read_bytes()).hexdigest()
              for f in ['cik_gvkey.pq','cik_ticker.pq']}
    report = dict(source=SOURCE,status='display_only_static_snapshot',
                  rule='valid links only; prefer Compustat Company; conflicting CIK/name labels unresolved',
                  identities=len(rows),tnic_unique_gvkeys_mapped=matched,
                  ambiguous_gvkeys=sorted(ambiguous),coverage=coverage,sha256=hashes,
                  ticker_rule='latest observed label(s) in source; not a historical ticker for selected year')
    (data/'names_audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['identities','tnic_unique_gvkeys_mapped']}))
    print('Latest year:',coverage[-1])
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--runtime',type=Path)
    a=p.parse_args();attach(a.source,a.data,a.runtime)
