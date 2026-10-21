"""Local peer explorer with on-demand financial-year preparation."""
import argparse
from functools import partial, lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse, parse_qs

if __package__:
    from .fusion import fusion_result
    from .text_jobs import status as text_status
    from .year_jobs import status as year_status
    from .sic import sic_peers
    from .text_similarity import score_peers, text_neighbours
    from .fundamentals import financial_peers
    from .overlap import overlap_result
else:
    from fusion import fusion_result
    from text_jobs import status as text_status
    from year_jobs import status as year_status
    from sic import sic_peers
    from text_similarity import score_peers, text_neighbours
    from fundamentals import financial_peers
    from overlap import overlap_result


@lru_cache(maxsize=32)
def cached_text_neighbours(path,stamp,year,gvkey):
    # Database mtime invalidates cached ranks when locally generated models change.
    with sqlite3.connect(path) as db:return text_neighbours(db,year,gvkey)


def has_names(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='identities'").fetchone() is not None


def label(db, gvkey, available):
    if available:
        row=db.execute('SELECT name,cik,ticker,ticker_last_seen,provider,status FROM identities WHERE gvkey=?',(gvkey,)).fetchone()
        if row:
            return dict(gvkey=gvkey,name=row[0],cik=row[1],ticker=row[2],ticker_last_seen=row[3],provider=row[4],status=row[5])
    return dict(gvkey=gvkey,name=None,ticker=None,status='unresolved')


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, data, **kwargs):
        self.data = data
        super().__init__(*args, **kwargs)

    def do_POST(self):
        url=urlparse(self.path)
        if url.path not in ('/api/financial/prepare','/api/tfidf/prepare'):return self.respond(404,b'{}')
        # Only the local application may trigger downloads and database writes.
        origin=self.headers.get('Origin')
        if self.headers.get('Sec-Fetch-Site')=='cross-site' or (origin and origin!=f'http://{self.headers.get("Host")}'):
            return self.respond(403,b'{}')
        try:
            year=int(parse_qs(url.query)['year'][0])
            return self.respond(200,json.dumps((text_status if url.path=='/api/tfidf/prepare' else year_status)(self.data,year,start=True,**({'section':parse_qs(url.query).get('section',['business'])[0]} if url.path=='/api/tfidf/prepare' else {}))).encode())
        except (ValueError,KeyError,IndexError):return self.respond(400,b'{}')

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/':
            return self.respond(200, (Path(__file__).with_name('explorer.html')).read_bytes(), 'text/html; charset=utf-8')
        if url.path in ('/api/financial/status','/api/tfidf/status'):
            try:return self.respond(200,json.dumps((text_status if url.path=='/api/tfidf/status' else year_status)(self.data,int(parse_qs(url.query)['year'][0]),**({'section':parse_qs(url.query).get('section',['business'])[0]} if url.path=='/api/tfidf/status' else {}))).encode())
            except (ValueError,KeyError,IndexError):return self.respond(400,b'{}')
        if url.path == '/api/audit':
            audit=json.loads((self.data/'audit.json').read_text())
            names=self.data/'names_audit.json'
            audit['names']=json.loads(names.read_text()) if names.exists() else None
            sic=self.data/'sic_audit.json'
            audit['sic']=json.loads(sic.read_text()) if sic.exists() else None
            return self.respond(200,json.dumps(audit).encode())
        if url.path=='/api/fusion':
            try:
                q=parse_qs(url.query);weights={key:float(q.get(key,['25'])[0]) for key in ('business','risk','mda','financial')}
                with sqlite3.connect(self.data/'tnic.sqlite') as db:result=fusion_result(db,int(q['year'][0]),int(q['gvkey'][0]),weights)
                return self.respond(200,json.dumps(result).encode())
            except (ValueError,KeyError):return self.respond(400,b'{"error":"Invalid fusion settings"}')
        if url.path in ('/api/financial','/api/tfidf','/api/overlap'):
            try:
                q=parse_qs(url.query);year=int(q.get('year',['2023'])[0]);gvkey=int(q.get('gvkey',['7343'])[0])
                path=self.data/'tnic.sqlite'
                if url.path=='/api/tfidf':
                    section=q.get('section',['business'])[0]
                    if section=='business':result=cached_text_neighbours(str(path.resolve()),path.stat().st_mtime_ns,year,gvkey)
                    else:
                        with sqlite3.connect(path) as db:result=text_neighbours(db,year,gvkey,section=section)
                else:
                    methods=q.get('methods',['tnic,sic'])[0].split(',')
                    text=cached_text_neighbours(str(path.resolve()),path.stat().st_mtime_ns,year,gvkey) if url.path=='/api/overlap' and 'tfidf' in methods else None
                    with sqlite3.connect(path) as db:
                        result=financial_peers(db,year,gvkey) if url.path=='/api/financial' else overlap_result(db,year,gvkey,int(q.get('digits',['3'])[0]),methods,text,{key:float(q.get(key,['25'])[0]) for key in ('business','risk','mda','financial')})
                return self.respond(200,json.dumps(result).encode())
            except (ValueError,IndexError):
                return self.respond(400,b'{"error":"Invalid year, GVKEY, grouping or method selection"}')
        if url.path == '/api/heatmap':
            try:
                q=parse_qs(url.query);year=int(q.get('year',['2023'])[0]);gvkey=int(q.get('gvkey',['7343'])[0]);digits=int(q.get('digits',['3'])[0])
                with sqlite3.connect(self.data/'tnic.sqlite') as db:
                    group=sic_peers(db,year,gvkey,digits,limit=100000)
                    if not group['available']:
                        return self.respond(200,json.dumps(group).encode())
                    result=score_peers(db,year,gvkey,group['peers'])
                return self.respond(200,json.dumps(result).encode())
            except (ValueError,IndexError):
                return self.respond(400,b'{"error":"Invalid year, GVKEY or SIC grouping"}')
        if url.path == '/api/sic':
            try:
                q=parse_qs(url.query)
                with sqlite3.connect(self.data/'tnic.sqlite') as db:
                    result=sic_peers(db,int(q.get('year',['2023'])[0]),
                                     int(q.get('gvkey',['7343'])[0]),
                                     int(q.get('digits',['3'])[0]),int(q.get('offset',['0'])[0]))
                return self.respond(200,json.dumps(result).encode())
            except (ValueError,IndexError):
                return self.respond(400,b'{"error":"Invalid year, GVKEY, SIC digits or offset"}')
        if url.path == '/api/search':
            try:
                q=parse_qs(url.query)
                year=int(q.get('year',['2023'])[0]);term=q.get('q',[''])[0].strip()[:120]
                with sqlite3.connect(self.data/'tnic.sqlite') as db:
                    available=has_names(db)
                    if not available or not term:
                        return self.respond(200,b'[]')
                    # Escape LIKE wildcards: search is literal, not a query language.
                    pattern='%'+term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                    found=db.execute('''SELECT i.gvkey FROM identities i JOIN firms f ON f.gvkey=i.gvkey
                        WHERE f.year=? AND (i.name LIKE ? ESCAPE '\\' OR i.ticker LIKE ? ESCAPE '\\'
                        OR CAST(i.gvkey AS TEXT)=?)
                        ORDER BY CASE WHEN upper(i.ticker)=upper(?) THEN 0 ELSE 1 END,i.name,i.gvkey LIMIT 15''',
                        (year,pattern,pattern,term,term)).fetchall()
                    results=[label(db,g,True) for (g,) in found]
                return self.respond(200,json.dumps(results).encode())
            except (ValueError, IndexError):
                return self.respond(400,b'{"error":"Invalid year"}')
        if url.path == '/api/peers':
            try:
                q = parse_qs(url.query)
                year = int(q.get('year',['2023'])[0])
                with sqlite3.connect(self.data/'tnic.sqlite') as db:
                    available=has_names(db)
                    if 'gvkey' in q:
                        gvkey = int(q['gvkey'][0])
                    else:
                        row=None
                        if available:
                            row=db.execute('''SELECT f.gvkey FROM firms f JOIN identities i USING(gvkey)
                                WHERE f.year=? ORDER BY CASE WHEN f.gvkey=7343 THEN 0 ELSE 1 END,f.gvkey LIMIT 1''',(year,)).fetchone()
                        if row is None:
                            row = db.execute('SELECT gvkey FROM firms WHERE year=? ORDER BY peer_count DESC,gvkey LIMIT 1',(year,)).fetchone()
                        if row is None: raise ValueError('Year not found')
                        gvkey = row[0]
                    count = db.execute('SELECT peer_count FROM firms WHERE year=? AND gvkey=?',(year,gvkey)).fetchone()
                    peers = db.execute('SELECT rank,peer,score FROM peers WHERE year=? AND gvkey=? ORDER BY rank',(year,gvkey)).fetchall()
                    company=label(db,gvkey,available)
                    peer_labels={str(p):label(db,p,available) for _,p,_ in peers}
                return self.respond(200, json.dumps(dict(year=year,gvkey=gvkey,found=count is not None,
                                      total_peers=count[0] if count else 0,peers=peers,
                                      company=company,peer_labels=peer_labels)).encode())
            except (ValueError, IndexError):
                return self.respond(400, b'{"error":"Invalid year or gvkey"}')
        self.respond(404,b'{"error":"Not found"}')

    def respond(self, status, body, mime='application/json'):
        self.send_response(status); self.send_header('Content-Type',mime)
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True);p.add_argument('--port',type=int,default=8766)
    a=p.parse_args()
    if not (a.data/'tnic.sqlite').is_file() or not (a.data/'audit.json').is_file():
        p.error('Run the TNIC index builder first')
    print(f'Open http://127.0.0.1:{a.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),partial(Handler,data=a.data)).serve_forever()
