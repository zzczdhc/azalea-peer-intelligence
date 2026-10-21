# Azalea Peer Intelligence

An English-language company research workspace for the NYU CDS Azalea capstone with S&P Global Ratings.

## Current milestone: filings and disclosed competitors

Enter a ticker or CIK, select filing dates and report types, and collect SEC 10-K / 10-Q primary documents. The workspace shows the company, filing date, financial period, processing status, SEC source, and extracted text. Text and metadata can be downloaded. Collections run in the background; repeating a collection reuses valid saved files.

The selected report also produces a Disclosed competitors panel. It extracts company names from explicit competition lists, preserves business context, deduplicates repeated names and statements, and checks name spelling against the cached SEC ticker directory. Filter the candidates by context, inspect the quoted evidence, and jump to the exact report passage. A saved annual report is selected first when available.

This is a rule-based candidate-generation baseline. It does not yet measure business similarity, rank peers, or generate company comparisons. A directory match is a name match rather than a reviewed ownership or financial-comparability decision. Unmatched names are retained for review; they are not classified as private companies.

## Shared online preview

[Open the public Azalea research preview](https://azalea-peer-intelligence-preview.lively-finch-8041.chatgpt.site).

The English read-only snapshot contains 4 companies (SPGI, MU, ORCL and RKLB) and 8 real SEC reports exported on October 14, 2026. Visitors can switch companies, filter business contexts, inspect source evidence, search complete report text, and download text and public metadata. New ticker collection remains in the local application. Updating local data does not update the hosted snapshot until it is exported and published again.

The export generator is `scripts/export_team_demo.py` (from the repository root); the existing managed Sites checkout remains at `../sites/azalea-team-demo/`, with its exact project identity in `.openai/hosting.json`. The checkout has its own source repository and is ignored by this parent repository. Public output excludes local configuration, contact email, run sessions, caches and database files.

## Run locally

Python 3.11 or later is required. Application and tests use the standard library; no package installation is necessary.

1. Create `.env.local` in the repository root with a `SEC_USER_AGENT=YourProject your-contact-email` line.
2. Set `SEC_USER_AGENT` to the project name and a real contact email. This identifies automated requests to SEC; it is not an email login. The local configuration is ignored by Git.
3. From the project directory, run:

```sh
python3 -m azalea_edgar.web --port 59942
```

Open `http://127.0.0.1:59942/`. The server binds only to the local machine. Keep the process running while using the workspace. Data is saved under the ignored `data/` directory; restarting the server preserves collected files.

An existing `SEC_USER_AGENT` environment variable takes precedence over `.env.local`. The collector can also be used directly:

```sh
python3 -m azalea_edgar sync --tickers SPGI --max-per-form 1 --data-dir data
python3 -m azalea_edgar audit --data-dir data
```

The CLI reads `SEC_USER_AGENT` from the environment. The web launcher also reads `.env.local`.

## Collection behavior

- Default date range: the past 365 days, based on the New York calendar date.
- Web interface: 10-K and/or 10-Q, latest 1–3 reports per form in the selected range.
- Filing date and report period are distinct and displayed separately.
- The issuer CIK and accession identify each filing. Recent and date-relevant historical submission indexes are checked.
- HTTP requests use a declared contact, pacing at 2 requests per second, bounded retries, and verified caches. SEC blocking stops further network access for that run.
- Raw documents, cleaned text, source metadata, hashes, and SQLite records are retained. Only validated ready documents are served by the reader.
- Existing source corrections are checked when “Check existing reports for source changes” is selected. Historical versions of the same accession are not retained.
- Extraction removes basic HTML and hidden content. It is not structured financial-table extraction, OCR, or a company business profile.
- The UI fetches SEC data through the local collector. It does not send requests directly from the browser.
- Report inspection and competitor extraction use saved text and the cached ticker directory, with no external requests. A missing directory leaves names available with unresolved identities.
- Competitor extraction requires a direct competition statement or a business list explicitly introduced as a source of competition. A Competition heading alone is insufficient. The UI displays the relationship basis and retains the list introduction. Covered patterns exclude customer/supplier/partner lists, unrelated lists later in the same paragraph, parenthetical joint-venture owners, and shareholder-return or compensation peer groups. These rules still require review and may miss unsupported wording.

## Checks

```sh
python3 -m unittest discover -s tests -v
```

The collector, web boundary and competitor-extraction tests use synthetic fixtures and do not access SEC. Real collection and extraction are checked separately in `VALIDATION.md`. Synthetic records are explicitly marked in the reader.

The collector, its original tests, and test fixtures were imported from the user-supplied `azalea_sec_prototype(1).zip`. The supplied synthetic demo library was not imported as research data.

## Next milestones

1. Extract source-linked business profiles and define the candidate company universe.
2. Establish an industry-based peer baseline and evaluate broader candidate retrieval and ranking.
3. Compare selected peers using filings and earnings call transcripts, with evidence and reporting-period alignment.
4. Add historical comparison and scheduled updates after the underlying behavior is validated.

## References

- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC automated access guidance](https://www.sec.gov/about/webmaster-frequently-asked-questions)
