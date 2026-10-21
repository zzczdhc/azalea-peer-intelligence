"""Descriptive TF-IDF cosine scores, independent of supplied TNIC scores."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import zipfile

# Explicit small English/filing stop list; no noun tagger or geographic filter.
STOP=set('''a an the and or but if while of at by for with about against between into through during before after above below to from up down in out on off over under again further then once here there when where why how all any both each few more most other some such no nor not only own same so than too very can will just should now is am are was were be been being have has had having do does did doing would could may might must shall this that these those it its itself they them their theirs themselves we us our ours ourselves you your yours yourselves he him his she her hers who whom which what as also including included include includes inc corporation corp company companies business fiscal year years item general approximately'''.split())
TOKEN=re.compile(r'[a-z]{3,40}')


def counts(text):
    return Counter(w for w in TOKEN.findall(text.lower()) if w not in STOP)


def tfidf(counter, df, n):
    values={term:(1+math.log(freq))*(math.log((1+n)/(1+df[term]))+1)
            for term,freq in counter.items() if 2 <= df[term] <= .8*n}
    norm=math.sqrt(sum(v*v for v in values.values()))
    return {t:v/norm for t,v in values.items()} if norm else {}


def cosine(a,b):
    if len(a)>len(b):a,b=b,a
    return max(0.0,min(1.0,sum(value*b.get(term,0.0) for term,value in a.items())))


class TextArchive:
    def __init__(self,source):self.source=source;self.zip=None
    def __enter__(self):
        if not self.source.is_dir():self.zip=zipfile.ZipFile(self.source)
        return self
    def __exit__(self,*args):
        if self.zip:self.zip.close()
    def namelist(self):
        return self.zip.namelist() if self.zip else [str(p.relative_to(self.source)) for p in self.source.rglob('*.txt')]
    def read(self,name):return self.zip.read(name) if self.zip else (self.source/name).read_bytes()


def build_text(source,data,year=2023,section="business"):
    if section not in ("business","risk","mda"):raise ValueError("Invalid section")
    vectors="text_vectors" if section=="business" else section+"_vectors"
    terms="text_terms" if section=="business" else section+"_terms"
    audit="text" if section=="business" else section
    source,data=Path(source),Path(data)
    with sqlite3.connect(data/'tnic.sqlite') as db:
        rows=db.execute('''WITH dated AS (
          SELECT *,row_number() over(partition by cik order by filed desc,adsh desc) rn
          FROM sic_filings WHERE form='10-K' AND filed BETWEEN ? AND ?),
        ids AS (SELECT i.* FROM identities i JOIN firms f USING(gvkey) WHERE f.year=?),
        unique_ciks AS (SELECT cik FROM ids GROUP BY cik HAVING count(*)=1)
        SELECT i.gvkey,x.adsh,x.filed FROM ids i JOIN unique_ciks u USING(cik)
        JOIN dated x ON CAST(i.cik AS INTEGER)=x.cik AND x.rn=1''',
        (f'{year}-01-01',f'{year}-12-31',year)).fetchall()
        corpus,df,skipped={},Counter(),Counter()
        with TextArchive(source) as archive:
            entries={Path(n).stem:n for n in archive.namelist() if n.endswith('.txt')}
            for gvkey,adsh,filed in rows:
                entry=entries.get(adsh)
                if not entry:
                    skipped['missing_accession_text']+=1;continue
                text=archive.read(entry).decode('utf-8-sig',errors='replace')
                words=counts(text)
                if len(text)<500 or sum(words.values())<100:
                    skipped['short_text']+=1;continue
                corpus[gvkey]=(words,adsh,filed,len(text));df.update(words.keys())
        n=len(corpus)
        if n<3:raise ValueError('Not enough matched annual business descriptions')
        vocabulary={term:i for i,term in enumerate(sorted(t for t,f in df.items() if 2 <= f <= .8*n))}
        db.executescript(f'''CREATE TABLE IF NOT EXISTS {vectors}(
            year INTEGER,gvkey INTEGER,vector TEXT,adsh TEXT,filed TEXT,characters INTEGER,
            PRIMARY KEY(year,gvkey));
            CREATE TABLE IF NOT EXISTS {terms}(year INTEGER,term_id INTEGER,term TEXT,
            PRIMARY KEY(year,term_id));''')
        db.execute(f'DELETE FROM {vectors} WHERE year=?',(year,));db.execute(f'DELETE FROM {terms} WHERE year=?',(year,))
        db.executemany(f'INSERT INTO {terms} VALUES (?,?,?)',[(year,i,t) for t,i in vocabulary.items()])
        for gvkey,(words,adsh,filed,chars) in corpus.items():
            vec=tfidf(words,df,n)
            values={str(vocabulary[t]):round(v,10) for t,v in vec.items()}
            if not values:skipped['empty_vector']+=1;continue
            db.execute(f'INSERT INTO {vectors} VALUES (?,?,?,?,?,?)',(year,gvkey,json.dumps(values,separators=(',',':')),adsh,filed,chars))
        vector_count=db.execute(f'SELECT count(*) FROM {vectors} WHERE year=?',(year,)).fetchone()[0]
    digest=hashlib.sha256()
    if source.is_dir():
        for g in sorted(corpus):
            digest.update(str(g).encode());digest.update(corpus[g][1].encode())
            digest.update((source/entries[corpus[g][1]]).read_bytes())
    else:
        with source.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    report=dict(year=year,status='descriptive_only_not_predictive_backtest',
                source=str(source.resolve()),sha256=digest.hexdigest(),candidate_filings=len(rows),
                documents=vector_count,vocabulary=len(vocabulary),skipped=dict(skipped),
                section={'business':'10-K Item 1 Business','risk':'10-K Item 1A Risk Factors','mda':'10-K Item 7 MD&A'}[section],filing_rule='latest non-amended 10-K filed within selected calendar year, exact accession match',
                formula='sublinear TF (1+ln count) × smoothed IDF (ln((1+N)/(1+df))+1), L2 normalization, cosine dot product',
                min_document_characters=500,min_remaining_tokens=100,min_df=2,max_df=.8,
                tokenizer='lowercase ASCII letter unigrams length 3–40; explicit English/filing stop list',
                stop_words=sorted(STOP),
                caveat='Independent lexical diagnostic; not a TNIC replication or a product-fit outcome test. Static identifiers are display-only. IDF fitted on the eligible descriptive-year corpus, not a train/test experiment.')
    (data/f'{audit}_audit_{year}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['year','documents','vocabulary','skipped']}),flush=True)
    return report


def score_peers(db,year,gvkey,group):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'text_vectors' not in tables:return dict(available=False,reason='Independent text similarity has not been computed.')
    focal=db.execute('SELECT vector,adsh,filed FROM text_vectors WHERE year=? AND gvkey=?',(year,gvkey)).fetchone()
    if not focal:return dict(available=False,reason='No matched annual business text for this company and year; missing scores are not zero.')
    a=json.loads(focal[0]);tnic=[p for (p,) in db.execute('SELECT peer FROM peers WHERE year=? AND gvkey=? ORDER BY rank',(year,gvkey))]
    sic_set={c['gvkey'] for c in group};ids=sorted(sic_set|set(tnic));results=[]
    lookup={c['gvkey']:c for c in group}
    for peer in ids:
        base=lookup.get(peer)
        if base is None:
            r=db.execute('SELECT name,ticker FROM identities WHERE gvkey=?',(peer,)).fetchone()
            base=dict(gvkey=peer,name=r[0] if r else None,ticker=r[1] if r else None)
        row=db.execute('SELECT vector,adsh,filed FROM text_vectors WHERE year=? AND gvkey=?',(year,peer)).fetchone()
        result=dict(gvkey=peer,name=base['name'],ticker=base.get('ticker'),in_sic=peer in sic_set,
                    in_tnic=peer in tnic,score=None,filed=None,adsh=None)
        if row:result.update(score=cosine(a,json.loads(row[0])),adsh=row[1],filed=row[2])
        results.append(result)
    def summary(predicate):
        selected=[c for c in results if predicate(c)]
        values=[c['score'] for c in selected if c['score'] is not None]
        return dict(total=len(selected),scored=len(values),mean=sum(values)/len(values) if values else None)
    return dict(available=True,year=year,gvkey=gvkey,metric='Independent TF-IDF cosine',
                focal_filed=focal[2],focal_accession=focal[1],companies=results,
                groups=dict(sic=summary(lambda c:c['in_sic']),tnic=summary(lambda c:c['in_tnic']),
                            sic_not_tnic=summary(lambda c:c['in_sic'] and not c['in_tnic'])))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--year',type=int,default=2023)
    a=p.parse_args();build_text(a.source,a.data,a.year)


def text_neighbours(db,year,gvkey,k=10,section="business"):
    if section not in ("business","risk","mda"):raise ValueError("Invalid section")
    table="text_vectors" if section=="business" else section+"_vectors"
    """Top-k over the complete eligible text corpus, never restricted to SIC."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
        return dict(available=False,reason='TF-IDF vectors have not been built.')
    focal=db.execute(f'SELECT vector,adsh,filed FROM {table} WHERE year=? AND gvkey=?',(year,gvkey)).fetchone()
    if not focal:return dict(available=False,reason=f'No matched annual {section} text for this company and year.')
    vector=json.loads(focal[0]);scores=[]
    for peer,value,adsh,filed in db.execute(f'SELECT gvkey,vector,adsh,filed FROM {table} WHERE year=? AND gvkey<>?',(year,gvkey)):
        scores.append((cosine(vector,json.loads(value)),peer,adsh,filed))
    scores.sort(key=lambda r:(-r[0],r[1]));peers=[]
    for rank,(score,peer,adsh,filed) in enumerate(scores[:k],1):
        label=db.execute('SELECT name,ticker FROM identities WHERE gvkey=?',(peer,)).fetchone()
        peers.append(dict(rank=rank,gvkey=peer,score=score,adsh=adsh,filed=filed,name=label[0] if label else None,ticker=label[1] if label else None))
    return dict(available=True,year=year,gvkey=gvkey,peers=peers,total_candidates=len(scores),focal_filed=focal[2])
