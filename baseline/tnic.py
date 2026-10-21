"""Build an audited TNIC-3 top-k index without third-party dependencies."""
import argparse
import csv
import hashlib
import heapq
import io
import json
import math
from pathlib import Path
import sqlite3
import statistics
import tempfile
from contextlib import ExitStack
import zipfile


def summarize(year, edges, db, k):
    groups = {}
    self_rows = 0
    asymmetric = 0
    for (firm, peer), score in edges.items():
        if firm == peer:
            self_rows += 1
            groups.setdefault(firm, [])
            continue
        groups.setdefault(firm, []).append((score, peer))
        if (peer, firm) not in edges or edges[(peer, firm)] != score:
            asymmetric += 1
    degrees = [len(v) for v in groups.values()]
    for firm, peers in groups.items():
        # Stable tie break: smaller gvkey first. Never backfill with non-TNIC firms.
        ranked = heapq.nsmallest(k, (p for p in peers if p[0] is not None),
                                key=lambda p: (-p[0], p[1]))
        db.execute('INSERT INTO firms VALUES (?,?,?)', (year, firm, len(peers)))
        db.executemany('INSERT INTO peers VALUES (?,?,?,?,?)',
                       [(year, firm, rank, peer, score)
                        for rank, (score, peer) in enumerate(ranked, 1)])
    return dict(year=year, rows=len(edges), self_rows=self_rows,
                nonself_rows=len(edges)-self_rows, firms=len(groups),
                mean_peers=round(statistics.mean(degrees), 3),
                median_peers=statistics.median(degrees),
                max_peers=max(degrees), fewer_than_k=sum(d < k for d in degrees),
                asymmetric_rows=asymmetric,
                missing_score_rows=sum(v is None for v in edges.values()))


def build(source, output, k=10):
    if k < 1:
        raise ValueError('k must be positive')
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output/'tnic.sqlite'
    if target.exists():
        raise FileExistsError(f'{target} already exists; use a new output folder')
    digest = hashlib.sha256()
    with source.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            digest.update(chunk)
    db = sqlite3.connect(target)
    db.executescript('''
    CREATE TABLE firms(year INTEGER, gvkey INTEGER, peer_count INTEGER,
                       PRIMARY KEY(year,gvkey));
    CREATE TABLE peers(year INTEGER, gvkey INTEGER, rank INTEGER,
                       peer INTEGER, score REAL, PRIMARY KEY(year,gvkey,rank));
    ''')
    audits = []
    try:
        # The official archive may append self rows after all annual pair rows.
        # Partition by year on disk before auditing; never assume source ordering.
        with tempfile.TemporaryDirectory(dir=output) as temp, ExitStack() as stack:
            buckets = {}
            with zipfile.ZipFile(source) as archive:
                with archive.open('tnic3_data.txt') as raw:
                    header = next(raw).decode().strip().split('\t')
                    if header != ['year', 'gvkey1', 'gvkey2', 'score']:
                        raise ValueError(f'Unexpected columns: {header}')
                    for line in raw:
                        year = int(line.split(b'\t', 1)[0])
                        if year not in buckets:
                            buckets[year] = stack.enter_context(open(Path(temp)/str(year), 'wb'))
                        buckets[year].write(line)
            for bucket in buckets.values():
                bucket.flush()
            def rows():
                for year in sorted(buckets):
                    with open(Path(temp)/str(year)) as f:
                        yield from csv.DictReader(f, fieldnames=header, delimiter='\t')
            reader = rows()
            current, edges = None, {}
            for row in reader:
                year, firm, peer = (int(row[n]) for n in ('year','gvkey1','gvkey2'))
                score = float(row['score']) if row['score'].strip() else None
                if score is not None and (not math.isfinite(score) or score < 0):
                    raise ValueError(f'Invalid score: {row}')
                if current is not None and year < current:
                    raise ValueError('Input must be sorted by year')
                if current is not None and year != current:
                    audits.append(summarize(current, edges, db, k))
                    db.commit()
                    print(f'Indexed {current}', flush=True)
                    edges = {}
                current = year
                if (firm, peer) in edges:
                    raise ValueError(f'Duplicate pair in {year}: {firm}, {peer}')
                edges[firm, peer] = score
            if current is None:
                raise ValueError('Empty TNIC data')
            audits.append(summarize(current, edges, db, k))
            db.commit()
        report = dict(source=str(source.resolve()), sha256=digest.hexdigest(), k=k,
                      min_year=audits[0]['year'], max_year=audits[-1]['year'],
                      rows=sum(a['rows'] for a in audits), yearly=audits,
                      status='descriptive_only_not_predictive_evaluation',
                      score_definition='excess over TNIC inclusion threshold; not raw cosine',
                      time_rule='year is fiscal-year label, not information availability date')
        (output/'audit.json').write_text(json.dumps(report, indent=2)+'\n')
        with (output/'yearly.csv').open('w') as f:
            w = csv.DictWriter(f, fieldnames=list(audits[0])); w.writeheader(); w.writerows(audits)
        with (output/'top_peers.csv').open('w') as f:
            w = csv.writer(f); w.writerow(['year','gvkey','rank','peer_gvkey','score'])
            w.writerows(db.execute('SELECT * FROM peers ORDER BY year,gvkey,rank'))
        return report
    except Exception:
        db.close()
        target.unlink(missing_ok=True)
        raise
    finally:
        db.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--k', type=int, default=10)
    args = p.parse_args()
    report = build(args.source, args.output, args.k)
    print(json.dumps({key: report[key] for key in ('rows','min_year','max_year','k')}))
