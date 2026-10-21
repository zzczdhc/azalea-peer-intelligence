"""Exact membership partitions for two to four selected peer methods."""
if __package__:
    from .sic import sic_peers
    from .fundamentals import financial_peers
    from .text_similarity import text_neighbours
else:
    from sic import sic_peers
    from fundamentals import financial_peers
    from text_similarity import text_neighbours

LABELS={'tnic':'TNIC','sic':'SIC','financial':'Financial','tfidf':'TF-IDF'}


def partition_sets(sets):
    """Regions are disjoint within the selected methods; return empty regions too."""
    names=list(sets);universe=set().union(*sets.values());groups={mask:[] for mask in range(1,1<<len(names))}
    for gvkey in sorted(universe):
        mask=sum(1<<i for i,name in enumerate(names) if gvkey in sets[name])
        groups[mask].append(gvkey)
    regions=[dict(mask=mask,members=[name for i,name in enumerate(names) if mask&(1<<i)],
                  count=len(ids),gvkeys=ids) for mask,ids in groups.items()]
    regions.sort(key=lambda r:(-len(r['members']),-r['count'],r['mask']))
    return regions


def overlap_result(db,year,gvkey,digits,methods,text_result=None):
    if len(methods)!=len(set(methods)) or not 2<=len(methods)<=4 or any(m not in LABELS for m in methods):
        raise ValueError('Choose two to four distinct available methods')
    sets={};notes=[]
    for method in methods:
        if method=='tnic':
            if not db.execute('SELECT 1 FROM firms WHERE year=? AND gvkey=?',(year,gvkey)).fetchone():
                return dict(available=False,reason='No TNIC record for the selected company/year.')
            ids=[r[0] for r in db.execute('SELECT peer FROM peers WHERE year=? AND gvkey=? ORDER BY rank',(year,gvkey))]
            notes.append('TNIC: displayed top peers (up to 10).')
        else:
            if method=='sic':
                result=sic_peers(db,year,gvkey,digits,limit=100000)
                notes.append(f'SIC: complete eligible {digits}-digit group, not a table page.')
            elif method=='financial':
                result=financial_peers(db,year,gvkey)
                notes.append('Financial: top 10 from complete five-feature financial profiles.')
            else:
                result=text_result if text_result is not None else text_neighbours(db,year,gvkey)
                notes.append('TF-IDF: top 10 from the full eligible Business-text corpus, not only SIC peers.')
            if not result['available']:
                return dict(available=False,reason=LABELS[method]+': '+result['reason'])
            ids=[c['gvkey'] for c in result['peers']]
        sets[method]=set(ids)-{gvkey}
    regions=partition_sets(sets);all_shared=set.intersection(*sets.values())
    def company(g):
        row=db.execute('SELECT name,ticker FROM identities WHERE gvkey=?',(g,)).fetchone()
        return dict(gvkey=g,name=row[0] if row else None,ticker=row[1] if row else None)
    for region in regions:region['companies']=[company(g) for g in region.pop('gvkeys')]
    return dict(available=True,year=year,gvkey=gvkey,digits=digits,
       sets=[dict(id=m,label=LABELS[m]+(f' {digits}-digit' if m=='sic' else ''),count=len(ids)) for m,ids in sets.items()],
       intersection_count=len(all_shared),intersection=[company(g) for g in sorted(all_shared)],
       union_count=len(set.union(*sets.values())),regions=regions,notes=notes)
