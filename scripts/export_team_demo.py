"""Export a public, read-only research preview without local configuration."""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from azalea_edgar.web import CollectorService


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f"Expected one source anchor: {before[:70]}")
    return text.replace(before, after, 1)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_preview(data_dir, destination):
    service = CollectorService(data_dir)
    library = service.library()
    snapshot_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    public = Path(destination) / "public"
    public.mkdir(parents=True, exist_ok=True)
    # Re-export only the current library; never retain removed filings.
    shutil.rmtree(public / "data", ignore_errors=True)
    company_fields = {"cik", "ticker", "name", "tickers", "exchanges", "sic", "sic_description", "fiscal_year_end", "source_url"}
    filing_fields = {"cik", "accession", "status", "form", "filing_date", "report_date", "source_url", "primary_document", "ticker", "company_name", "characters", "words", "fetched_at", "data_kind", "warnings"}
    metadata_fields = filing_fields | {"text_sha256", "raw_sha256", "cleaner_version", "processed_at", "sic", "sic_description", "company_metadata_source_url", "company_metadata_observed_at"}
    records = []
    for record in library["filings"]:
        if record["status"] != "ready":
            continue
        body, _, artifact = service.document(record["cik"], record["accession"], "text")
        if artifact.get("data_kind") != "live_sec":
            raise ValueError("The shared library must contain real SEC reports only.")
        text = body.decode("utf-8")
        analysis = service.competitors(text, artifact)
        for group in analysis["groups"]:
            if text[group["start"]:group["end"]] != group["quote"]:
                raise ValueError("Evidence does not match the saved report.")
        metadata = {k: v for k, v in artifact.items() if k in metadata_fields}
        metadata.update(integrity_checked_at_export=True, exported_at=snapshot_at)
        folder = public / "data" / "documents" / record["cik"] / record["accession"]
        write_json(folder / "preview.json", {"text": text, "metadata": metadata, "truncated": False, "competitors": analysis})
        write_json(folder / "metadata.json", metadata)
        (folder / "text.txt").write_bytes(body)
        records.append({k: v for k, v in record.items() if k in filing_fields})
    covered = {r["cik"] for r in records}
    companies = [{k: v for k, v in c.items() if k in company_fields} for c in library["companies"] if c["cik"] in covered]
    if not records:
        raise ValueError("No real saved reports to publish.")
    write_json(public / "data" / "library.json", {"companies": companies, "filings": records, "last_run": None, "exported_at": snapshot_at})
    write_json(public / "data" / "config.json", {"read_only": True, "since": "2025-10-14", "until": "2026-10-14", "active_job": None, "contact_configured": False})

    html = (ROOT / "azalea_edgar/static/index.html").read_text()
    html = replace_once(html, "<title>Azalea — Company filings</title>", "<title>Azalea — Shared research preview</title>")
    html = replace_once(html, '<section class="collection-panel" aria-labelledby="collection-heading">', '<section class="collection-panel" aria-labelledby="collection-heading" hidden>')
    banner = f'''<section class="demo-banner" aria-label="Shared research preview">
          <div class="eyebrow">SHARED RESEARCH PREVIEW</div>
          <h2>{len(companies)} companies. {len(records)} real SEC reports. Every candidate has a source.</h2>
          <p>Switch companies below to explore saved filings, business contexts and disclosed competitor candidates.</p>
          <p class="demo-scope">This version uses an exported library. Analysis was generated from these reports; collecting a new company is available in the local workspace.</p>
          <details class="demo-algorithm"><summary>How the extraction works</summary><ol>
          <li>Find direct competition statements, such as “We compete with”, or a business list explicitly introduced as a source of competition.</li>
          <li>Keep the list inside the relevant sentence or introduced business context. Stop when the text changes to customers, suppliers or partners.</li>
          <li>Split the enumeration into company-name candidates. Preserve legal-name punctuation and exclude parenthetical joint-venture owners.</li>
          <li>Merge repeated names and statements while retaining the business contexts for each company.</li>
          <li>Match normalized names against the cached SEC ticker directory. A name match is an identity clue, not proof of competition.</li>
          <li>Keep the source quote and its text offsets so every candidate can be checked in the full report.</li>
          </ol><p>This is a rule-based baseline, not a trained language model or a similarity ranking. It takes the first supported named list in a paragraph and can miss other wording. Market-wide precision and recall have not been measured.</p></details>
        </section>
        '''
    html = replace_once(html, '<section class="library-section"', banner + '<section class="library-section"')
    html = replace_once(html, "Refresh library</button>", "Reload library</button>")
    html = replace_once(html, "Collect and inspect the reports behind your company research.", "Explore company filings and the evidence behind competitor candidates.")
    (public / "index.html").write_text(html, encoding="utf-8")

    js = (ROOT / "azalea_edgar/static/app.js").read_text()
    js = replace_once(js, "  const response = await fetch(path, { cache: 'no-store', ...options });", """  if (options?.method && options.method !== 'GET') throw new Error('This shared preview does not collect new filings.');
  let file;
  if (path === '/api/config') file = '/data/config.json';
  else if (path === '/api/library') file = '/data/library.json';
  else if (/^\\/documents\\/[0-9]{10}\\/[0-9]{10}-[0-9]{2}-[0-9]{6}\\/text$/.test(path)) file = path.replace('/documents/', '/data/documents/').replace(/\\/text$/, '/preview.json');
  else throw new Error('This action is not available in the shared preview.');
  const response = await fetch(file, { cache: 'no-store' });""")
    js = replace_once(js, "  $('last-run-label').textContent = report ? `Last collection: ${new Date(report.finished_at).toLocaleString('en-US')}` : 'No collection yet';", "  $('last-run-label').textContent = `Library snapshot: ${new Date(state.library.exported_at).toLocaleString('en-US', {timeZone:'America/New_York'})} ET`;")
    js = replace_once(js, "  $('reader-download').href = `${base}/text?download=1`;", "  $('reader-download').href = `${base.replace('/documents/', '/data/documents/')}/text.txt`;\n  $('reader-download').download = `${record.ticker}-${record.form}-${record.report_date}.txt`;")
    js = replace_once(js, "  $('reader-metadata').href = `${base}/metadata?download=1`;", "  $('reader-metadata').href = `${base.replace('/documents/', '/data/documents/')}/metadata.json`;\n  $('reader-metadata').download = `${record.ticker}-${record.form}-${record.report_date}-metadata.json`;")
    js = replace_once(js, "'File integrity checked'", "'Integrity checked at export'")
    js = replace_once(js, "    await loadLibrary();\n    if (config.active_job)", "    await loadLibrary(new URLSearchParams(location.search).get('company') || 'RKLB');\n    if (config.read_only) { status('Shared library loaded.'); return; }\n    if (config.active_job)")
    js += "\n$('company-filter').addEventListener('change', () => { const url = new URL(location.href); url.searchParams.set('company', $('company-filter').value); history.replaceState(null, '', url); });\n"
    (public / "app.js").write_text(js, encoding="utf-8")
    css = (ROOT / "azalea_edgar/static/styles.css").read_text()
    css += "\n.demo-banner{padding:23px;background:#edf3ee;border:1px solid #d4e0d4;border-radius:10px}.demo-banner h2{font-size:18px;line-height:1.5}.demo-banner p{margin-top:9px;font-size:13px;color:#536b59}.demo-banner .demo-scope{font-size:12px}.demo-algorithm{margin-top:15px;color:#385b42;font-size:12px}.demo-algorithm summary{cursor:pointer;width:fit-content}.demo-algorithm ol{padding-left:20px}.demo-algorithm li{margin:7px 0}\n"
    (public / "styles.css").write_text(css, encoding="utf-8")
    (public / "favicon.svg").write_bytes((ROOT / "azalea_edgar/static/favicon.svg").read_bytes())
    # The public bundle must never carry contact configuration, local paths or sessions.
    for path in public.rglob("*"):
        if path.is_file() and (path.name in {"config.json", "library.json", "metadata.json", "preview.json", "app.js", "index.html"}):
            content = path.read_text(encoding="utf-8")
            if "sw5133@nyu.edu" in content or "/Users/wushuang/" in content or '"raw_path"' in content or '"metadata_path"' in content:
                raise ValueError(f"Private local configuration found in export: {path.name}")
    return {"companies": len(companies), "filings": len(records), "exported_at": snapshot_at, "destination": str(Path(destination).resolve())}


if __name__ == "__main__":
    destination = ROOT / "sites" / "azalea-team-demo"
    print(json.dumps(export_preview(ROOT / "data", destination), indent=2))
