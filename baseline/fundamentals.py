"""Financial-only nearest neighbours from as-filed SEC primary statements."""
import argparse
from collections import defaultdict, Counter
import csv
from datetime import datetime
import io
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import zipfile

REVENUE=['RevenueFromContractWithCustomerExcludingAssessedTax','RevenueFromContractWithCustomerIncludingAssessedTax','Revenues','SalesRevenueNet']
INCOME=['NetIncomeLoss','ProfitLoss']
PPE=['PropertyPlantAndEquipmentNet','PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization']
TAGS=set(REVENUE+INCOME+PPE+['Assets','Liabilities','LiabilitiesCurrent','LiabilitiesNoncurrent','PropertyPlantAndEquipmentNet'])
FEATURES=[('log_assets','Log assets'),('net_margin','Net margin'),('revenue_growth','Revenue growth'),('liabilities_assets','Liabilities / assets'),('ppe_assets','Net PPE / assets')]


def annual_filings(db,year):
    return db.execute('''WITH dated AS (
      SELECT *,row_number() over(partition by cik order by filed desc,adsh desc) rn
      FROM sic_filings WHERE form='10-K' AND filed BETWEEN ? AND ?),
    ids AS (SELECT i.* FROM identities i JOIN firms f USING(gvkey) WHERE f.year=?),
    unique_ciks AS (SELECT cik FROM ids GROUP BY cik HAVING count(*)=1)
    SELECT i.gvkey,x.adsh,x.filed,x.period FROM ids i JOIN unique_ciks u USING(cik)
    JOIN dated x ON CAST(i.cik AS INTEGER)=x.cik AND x.rn=1''',
    (f'{year}-01-01',f'{year}-12-31',year)).fetchall()


def one(facts,tag,end,qtrs):
    values=facts.get((tag,end,qtrs),set())
    return next(iter(values)) if len(values)==1 else None


def feature_values(facts,end):
    """Require consistent periods/units. Missing/conflicting facts are never zero."""
    sources={}
    def choose(tags,qtrs):
        for tag in tags:
            value=one(facts,tag,end,qtrs)
            if value is not None:return value,tag
        return None,None
    assets,at=choose(['Assets'],0)
    liabilities,lt=choose(['Liabilities'],0)
    if liabilities is None:
        a=one(facts,'LiabilitiesCurrent',end,0);b=one(facts,'LiabilitiesNoncurrent',end,0)
        if a is not None and b is not None:liabilities=a+b;lt='LiabilitiesCurrent + LiabilitiesNoncurrent'
    ppe,pt=choose(PPE,0)
    revenue,rt=choose(REVENUE,4)
    income,it=choose(INCOME,4)
    required={'assets':assets,'liabilities':liabilities,'net_ppe':ppe,'revenue':revenue,'net_income':income}
    absent=[k for k,v in required.items() if v is None]
    if absent:return None,'Missing or ambiguous: '+', '.join(absent)
    if assets<=0 or revenue<=0 or liabilities<0 or ppe<0:
        return None,'Invalid assets/revenue denominator or negative liabilities/PPE'
    date=datetime.strptime(end,'%Y%m%d')
    previous=[]
    for (tag,ddate,qtrs),values in facts.items():
        if tag!=rt or qtrs!=4 or len(values)!=1:continue
        try:days=(date-datetime.strptime(ddate,'%Y%m%d')).days
        except ValueError:continue
        if 300<=days<=430:previous.append((abs(days-365),ddate,next(iter(values))))
    if not previous:return None,'No comparable prior annual revenue using the same tag'
    _,previous_date,prior=sorted(previous)[0]
    if prior<=0:return None,'Nonpositive prior-year revenue'
    features=dict(log_assets=math.log(assets),net_margin=income/revenue,
                  revenue_growth=revenue/prior-1,liabilities_assets=liabilities/assets,ppe_assets=ppe/assets)
    if not all(math.isfinite(v) for v in features.values()):return None,'Non-finite derived feature'
    sources.update(assets=at,liabilities=lt,net_ppe=pt,revenue=rt,net_income=it)
    raw=dict(assets=assets,liabilities=liabilities,net_ppe=ppe,revenue=revenue,net_income=income,prior_revenue=prior,prior_period=previous_date)
    return dict(features=features,raw=raw,tags=sources),None


def quantile(values,p):
    v=sorted(values);x=(len(v)-1)*p;lo=math.floor(x);hi=math.ceil(x)
    return v[lo]+(v[hi]-v[lo])*(x-lo)


def normalize(records):
    parameters={};vectors={g:{} for g in records}
    for key,_ in FEATURES:
        values=[r['features'][key] for r in records.values()]
        low,high=quantile(values,.01),quantile(values,.99)
        clipped=[min(high,max(low,v)) for v in values]
        mean=statistics.mean(clipped);sd=statistics.pstdev(clipped)
        if sd==0:raise ValueError('Constant financial feature: '+key)
        parameters[key]=dict(lower=low,upper=high,mean=mean,std=sd)
        for g,r in records.items():vectors[g][key]=(min(high,max(low,r['features'][key]))-mean)/sd
    return vectors,parameters


def build_fundamentals(source,data,year=2023):
    source,data=Path(source),Path(data)
    if any(not (source/f'{year}q{q}.zip').is_file() for q in range(1,5)):
        raise ValueError('All four SEC quarterly archives are required for this annual cohort')
    with sqlite3.connect(data/'tnic.sqlite') as db:
        filings=annual_filings(db,year);wanted={a for _,a,_,_ in filings};facts=defaultdict(lambda:defaultdict(set))
        ignored=Counter()
        for path in sorted(source.glob(f'{year}q[1-4].zip')):
            with zipfile.ZipFile(path) as archive:
                with archive.open('num.txt') as raw:
                    for row in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig'),delimiter='\t'):
                        if row['adsh'] not in wanted or row['tag'] not in TAGS:continue
                        if row['uom']!='USD' or row.get('segments') or row.get('coreg') or not row['version'].startswith('us-gaap/'):
                            ignored['nonconsolidated_nonUSD_or_custom']+=1;continue
                        try:qtrs=int(row['qtrs']);value=float(row['value'])
                        except (ValueError,TypeError):continue
                        if qtrs not in (0,4) or not math.isfinite(value):continue
                        facts[row['adsh']][row['tag'],row['ddate'],qtrs].add(value)
            print('Read financial facts:',path.name,flush=True)
        records,exclusions={},[]
        for gvkey,adsh,filed,end in filings:
            item,reason=feature_values(facts.get(adsh,{}),end)
            if item is None:exclusions.append((year,gvkey,reason));continue
            item.update(adsh=adsh,filed=filed,period=end);records[gvkey]=item
        if len(records)<3:raise ValueError('Too few complete financial profiles')
        vectors,parameters=normalize(records)
        db.executescript('''CREATE TABLE IF NOT EXISTS financial_profiles(
           year INTEGER,gvkey INTEGER,profile TEXT,vector TEXT,PRIMARY KEY(year,gvkey));
           CREATE TABLE IF NOT EXISTS financial_exclusions(year INTEGER,gvkey INTEGER,reason TEXT,PRIMARY KEY(year,gvkey));
           CREATE TABLE IF NOT EXISTS financial_models(year INTEGER PRIMARY KEY,parameters TEXT);''')
        for table in ['financial_profiles','financial_exclusions','financial_models']:db.execute(f'DELETE FROM {table} WHERE year=?',(year,))
        db.executemany('INSERT INTO financial_profiles VALUES (?,?,?,?)',[(year,g,json.dumps(r),json.dumps(vectors[g])) for g,r in records.items()])
        db.executemany('INSERT INTO financial_exclusions VALUES (?,?,?)',exclusions)
        db.execute('INSERT INTO financial_models VALUES (?,?)',(year,json.dumps(parameters)))
    sources=[]
    for path in sorted(source.glob(f'{year}q[1-4].zip')):
        digest=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
        sources.append(dict(file=path.name,sha256=digest.hexdigest()))
    report=dict(source=str(source.resolve()),sources=sources,year=year,candidate_filings=len(filings),complete_profiles=len(records),excluded=len(exclusions),
       exclusions=dict(Counter(reason for _,_,reason in exclusions)),ignored=dict(ignored),
       features=[dict(key=k,label=l) for k,l in FEATURES],parameters=parameters,
       rule='Latest non-amended annual filing in calendar year; annual USD US-GAAP consolidated facts; complete cases only; no missing-to-zero or imputation.',
       scaling='1st/99th percentile winsorization then population z-score within descriptive-year complete-case cohort; equal feature weight',
       distance='Euclidean distance over five standardized financial features, lower is closer',
       ppe_definition='Net PPE; uses reported combined net PPE plus finance-lease ROU asset tag when plain net PPE is not available. Selected tags are retained for each company.',
       leverage_definition='Liabilities / assets, not debt / assets. Liabilities may be the sum of reported current and noncurrent liabilities.',
       status='descriptive_baseline_not_a_predictive_backtest',
       caveat='Identity links are display snapshots; annual fiscal periods may differ. Fixed historical train/test transforms are required for predictive evaluation.')
    (data/f'financial_audit_{year}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(year=year,complete=len(records),excluded=len(exclusions))),flush=True)
    return report


def financial_peers(db,year,gvkey,k=10):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='financial_profiles'").fetchone():
        return dict(available=False,reason='Financial profiles have not been built.')
    focal=db.execute('SELECT profile,vector FROM financial_profiles WHERE year=? AND gvkey=?',(year,gvkey)).fetchone()
    if not focal:
        excluded=db.execute('SELECT reason FROM financial_exclusions WHERE year=? AND gvkey=?',(year,gvkey)).fetchone()
        return dict(available=False,reason=excluded[0] if excluded else 'No complete financial profile for this company and year.')
    target=json.loads(focal[1]);rows=[]
    for peer,profile,vector in db.execute('SELECT gvkey,profile,vector FROM financial_profiles WHERE year=? AND gvkey<>?',(year,gvkey)):
        v=json.loads(vector);contributions={key:(target[key]-v[key])**2 for key,_ in FEATURES}
        distance=math.sqrt(sum(contributions.values()))
        rows.append((distance,peer,profile,contributions))
    rows.sort(key=lambda r:(r[0],r[1]));results=[]
    for rank,(distance,peer,profile,contributions) in enumerate(rows[:k],1):
        label=db.execute('SELECT name,ticker,cik FROM identities WHERE gvkey=?',(peer,)).fetchone()
        p=json.loads(profile)
        results.append(dict(rank=rank,gvkey=peer,distance=distance,name=label[0] if label else None,
          ticker=label[1] if label else None,cik=label[2] if label else None,profile=p,contributions=contributions))
    params=db.execute('SELECT parameters FROM financial_models WHERE year=?',(year,)).fetchone()
    return dict(available=True,year=year,gvkey=gvkey,company=json.loads(focal[0]),features=[dict(key=k,label=l) for k,l in FEATURES],
                peers=results,total_candidates=len(rows),parameters=json.loads(params[0]))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--year',type=int,default=2023)
    a=p.parse_args();build_fundamentals(a.source,a.data,a.year)
