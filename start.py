"""Configure local sources and launch the baseline explorer (Python standard library)."""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from baseline.serve import Handler


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,default=Path('outputs/tnic-baseline'))
    p.add_argument('--business-text',type=Path)
    p.add_argument('--risk-text',type=Path)
    p.add_argument('--mda-text',type=Path)
    p.add_argument('--sec-contact',help='Your contact email sent to SEC with automated requests')
    p.add_argument('--tnic-source',type=Path,help='Build the initial index from tnic3_data.zip if absent')
    p.add_argument('--port',type=int,default=8766)
    a=p.parse_args()
    if not (a.data/'tnic.sqlite').is_file():
        if not a.tnic_source:p.error('Initial index missing. Supply --tnic-source /path/to/tnic3_data.zip, then attach company names/CIK mapping as described in baseline/README.md.')
        from baseline.tnic import build
        build(a.tnic_source,a.data)
    if not (a.data/'audit.json').is_file():p.error('TNIC audit missing; rebuild into a new output directory.')
    for section,path in [('business',a.business_text),('risk',a.risk_text),('mda',a.mda_text)]:
        if path:
            if not path.exists():p.error(section+' text source not found')
            (a.data/f'.{section}-text-source').write_text(str(path.resolve())+'\n')
    if a.sec_contact:
        if '@' not in a.sec_contact or any(c in a.sec_contact for c in '\r\n'):p.error('Supply a valid contact email')
        contact=a.data/'.sec-user-agent';contact.write_text('AzaleaPeerIntelligence/1.0 academic research '+a.sec_contact+'\n');contact.chmod(0o600)
    print(f'Open http://127.0.0.1:{a.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),partial(Handler,data=a.data)).serve_forever()

if __name__=='__main__':main()
