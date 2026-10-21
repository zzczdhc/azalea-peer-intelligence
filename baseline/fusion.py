"""Weighted late fusion on a common complete-case candidate universe."""
import json
import math
from bisect import bisect_left,bisect_right
try:
    from .text_similarity import cosine
except ImportError:
    from text_similarity import cosine


def percentiles(values):
    ordered=sorted(values.values());n=len(ordered)
    return {k:((bisect_left(ordered,v)+bisect_right(ordered,v)-1)/2)/(n-1) if n>1 else .5 for k,v in values.items()}


def fuse_scores(signals,weights):
    active={k:v for k,v in weights.items() if v>0}
    if not active or any(not math.isfinite(v) or v<0 for v in weights.values()):raise ValueError('Use finite nonnegative weights with a positive total')
    ids=set.intersection(*(set(signals[k]) for k in active))
    calibrated={k:percentiles({g:signals[k][g] for g in ids}) for k in active}
    total=sum(active.values());normalized={k:v/total for k,v in active.items()}
    result=[]
    for g in ids:
        contributions={k:normalized[k]*calibrated[k][g] for k in active}
        result.append(dict(gvkey=g,score=sum(contributions.values()),raw={k:signals[k][g] for k in active},percentiles={k:calibrated[k][g] for k in active},contributions=contributions))
    return sorted(result,key=lambda r:(-r['score'],r['gvkey'])),normalized


def fusion_result(db,year,gvkey,weights,k=10):
    if set(weights)-{'business','risk','mda','financial'}:raise ValueError('Invalid signal')
    signals={};sources={};coverage={}
    for section,weight in weights.items():
        if weight<=0:continue
        table='financial_profiles' if section=='financial' else ('text_vectors' if section=='business' else section+'_vectors')
        if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():return dict(available=False,reason=f'{section} has not been prepared.')
        if section=='financial':
            rows=db.execute(f'SELECT gvkey,vector,profile FROM {table} WHERE year=?',(year,)).fetchall()
            vectors={g:json.loads(v) for g,v,_ in rows};sources[section]={g:json.loads(p) for g,_,p in rows}
        else:
            rows=db.execute(f'SELECT gvkey,vector,adsh,filed FROM {table} WHERE year=?',(year,)).fetchall()
            vectors={g:json.loads(v) for g,v,_,_ in rows};sources[section]={g:dict(adsh=a,filed=f) for g,_,a,f in rows}
        coverage[section]=len(vectors)
        if gvkey not in vectors:return dict(available=False,reason=f'Target company has no matched {section} profile for {year}. No missing-to-zero substitution.')
        focal=vectors[gvkey]
        signals[section]={g:(-math.sqrt(sum((focal[t]-v[t])**2 for t in focal)) if section=='financial' else cosine(focal,v)) for g,v in vectors.items() if g!=gvkey}
    ranked,normalized=fuse_scores(signals,weights)
    for rank,row in enumerate(ranked[:k],1):
        label=db.execute('SELECT name,ticker FROM identities WHERE gvkey=?',(row['gvkey'],)).fetchone()
        row.update(rank=rank,name=label[0] if label else None,ticker=label[1] if label else None,sources={s:sources[s][row['gvkey']] for s in normalized})
        if 'financial' in row['raw']:row['raw']['financial']=-row['raw']['financial']
    return dict(available=True,year=year,gvkey=gvkey,weights=normalized,total_candidates=len(ranked),coverage=coverage,peers=ranked[:k],method='Per-target percentile ranks (average ties) within common candidate pool; negative financial distance before ranking; weighted sum. Descriptive exploratory weights, not optimized or evaluated.')
