"""Import as-filed SEC SIC codes and build descriptive industry peer groups."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import zipfile

SEC_DATA='https://www.sec.gov/dera/data/financial-statement-data-sets.html'


def import_sic(source, data):
    source,data=Path(source),Path(data)
    files=sorted(source.glob('*q[1-4].zip'))
    if not files: raise ValueError('No SEC quarterly ZIPs found')
    rows,provenance={},[]
    for path in files:
        name=path.stem
        if not re.fullmatch(r'\d{4}q[1-4]',name): continue
        count=0
        with zipfile.ZipFile(path) as archive:
            with archive.open('sub.txt') as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig'),delimiter='\t'):
                    if row['form'] not in ('10-K','10-Q','10-K/A','10-Q/A'):continue
                    sic=row['sic'].strip()
                    if not sic.isdigit() or not 100 <= int(sic) <= 9999:continue
                    cik=int(row['cik']);filed=row['filed']
                    if not re.fullmatch(r'\d{8}',filed):continue
                    date=f'{filed[:4]}-{filed[4:6]}-{filed[6:]}'
                    rows[row['adsh']]=(row['adsh'],cik,row['name'],sic.zfill(4),date,row['period'],row['form'],name)
                    count+=1
        sha=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):sha.update(chunk)
        provenance.append(dict(file=name+'.zip',sha256=sha.hexdigest(),eligible_filings=count,
            url=f'https://www.sec.gov/files/dera/data/financial-statement-data-sets/{name}.zip'))
    with sqlite3.connect(data/'tnic.sqlite') as db:
        db.execute('DROP TABLE IF EXISTS sic_filings')
        db.execute('''CREATE TABLE sic_filings(adsh TEXT PRIMARY KEY,cik INTEGER,
            filing_name TEXT,sic TEXT,filed TEXT,period TEXT,form TEXT,source_quarter TEXT)''')
        db.executemany('INSERT INTO sic_filings VALUES (?,?,?,?,?,?,?,?)',rows.values())
        db.execute('CREATE INDEX sic_cik_date ON sic_filings(cik,filed)')
    report=dict(source=SEC_DATA,files=provenance,filings=len(rows),
                years=sorted({int(v[4][:4]) for v in rows.values()}),
                status='descriptive_SIC_groups_with_static_identifier_links',
                policy='latest eligible 10-K/10-Q submission in selected calendar year, filed on or before December 31; unique CIK per TNIC-year universe')
    (data/'sic_audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(filings=report['filings'],years=report['years'])))
    return report


def sic_peers(db, year, gvkey, digits=3, offset=0, limit=25):
    if digits not in (2,3):raise ValueError('SIC grouping must have 2 or 3 digits')
    if offset < 0:raise ValueError('Offset must be nonnegative')
    required={'identities','sic_filings'}
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required.issubset(tables):
        return dict(available=False,reason='SIC source or company identifier mapping has not been loaded.')
    lo,hi=f'{year}-01-01',f'{year}-12-31'
    if not db.execute('SELECT 1 FROM sic_filings WHERE filed BETWEEN ? AND ? LIMIT 1',(lo,hi)).fetchone():
        return dict(available=False,reason=f'No SEC SIC observations loaded for {year}. No later-year codes are substituted.')
    query='''WITH dated AS (
      SELECT *,ROW_NUMBER() OVER (PARTITION BY cik ORDER BY filed DESC,adsh DESC) AS sequence
      FROM sic_filings WHERE filed BETWEEN ? AND ?),
    candidates AS (
      SELECT f.gvkey,i.name,i.ticker,i.cik,d.sic,d.filed,d.adsh,d.filing_name,d.period
      FROM firms f JOIN identities i USING(gvkey)
      JOIN dated d ON CAST(i.cik AS INTEGER)=d.cik AND d.sequence=1 WHERE f.year=?),
    unique_ciks AS (SELECT cik FROM candidates GROUP BY cik HAVING COUNT(*)=1)
    SELECT c.* FROM candidates c JOIN unique_ciks u USING(cik)'''
    columns=['gvkey','name','ticker','cik','sic','filed','adsh','filing_name','period']
    universe=[dict(zip(columns,r)) for r in db.execute(query,(lo,hi,year))]
    by_id={c['gvkey']:c for c in universe}
    focal=by_id.get(gvkey)
    total=db.execute('SELECT count(*) FROM firms WHERE year=?',(year,)).fetchone()[0]
    common=dict(as_of=hi,source_year=year,digits=digits,universe_total=total,
                sic_covered=len(universe),universe='selected-year TNIC companies with unique display mapping and observed SEC SIC',
                source=SEC_DATA)
    if not focal:
        return dict(available=False,reason='No unambiguous SEC SIC match for this company in this year.',**common)
    tnic={p for (p,) in db.execute('SELECT peer FROM peers WHERE year=? AND gvkey=?',(year,gvkey))}
    prefix=focal['sic'][:digits]
    group=sorted([c for c in universe if c['gvkey']!=gvkey and c['sic'].startswith(prefix)],key=lambda c:(c['name'].casefold(),c['gvkey']))
    for c in [focal]+group:
        c['source_url']=f"https://www.sec.gov/Archives/edgar/data/{int(c['cik'])}/{c['adsh'].replace('-','')}/{c['adsh']}-index.htm"
        c['in_tnic_top_k']=c['gvkey'] in tnic
    counts={str(n):sum(c['gvkey']!=gvkey and c['sic'].startswith(focal['sic'][:n]) for c in universe) for n in (2,3)}
    return dict(available=True,company=focal,prefix=prefix,total_peers=len(group),
                peers=group[offset:offset+limit],offset=offset,limit=limit,
                group_counts=counts,tnic_displayed=len(tnic),tnic_sic_covered=len(tnic & by_id.keys()),
                overlap=sum(c['gvkey'] in tnic for c in group),
                overlap_peers=[c for c in group if c['gvkey'] in tnic],**common)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--data',type=Path,required=True)
    a=p.parse_args();import_sic(a.source,a.data)
