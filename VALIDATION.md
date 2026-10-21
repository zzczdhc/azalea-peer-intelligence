# Azalea validation

Validated on October 14, 2026.

## Real SEC collection

The collector downloaded two real primary documents for S&P Global Inc. (SPGI, CIK 0000064040), within the filing-date window October 14, 2025 through October 14, 2026.

| Form | Filed | Financial period ended | Extracted words | Source |
| --- | --- | --- | ---: | --- |
| 10-K | 2026-02-11 | 2025-12-31 | 78,695 | [SEC original](https://www.sec.gov/Archives/edgar/data/64040/000006404026000013/spgi-20251231.htm) |
| 10-Q | 2026-07-28 | 2026-06-30 | 31,517 | [SEC original](https://www.sec.gov/Archives/edgar/data/64040/000006404026000045/spgi-20260630.htm) |

The initial CIK-based run processed both documents with zero failures. The web interface subsequently collected SPGI by ticker and reused both valid documents, processing zero additional documents with zero failures. The independent local audit checked both stored records and returned 2 valid, 0 invalid.

The document reader displayed both the annual and quarterly report text. The extracted opening text matched the form, issuer, and financial period. Raw documents, cleaned text, metadata, and run reports are saved under the ignored `data/` directory.

## Automated checks

`python3 -m unittest discover -s tests -v` passed all 69 tests:

- 39 tests imported with the original collector.
- 15 tests covering web input validation, background collection and reuse, shared request pacing across consecutive jobs, concurrent-job prevention, document integrity, source downloads, English HTML, HTTP request boundaries, complete text beyond the former preview limit, and source-linked competitor results without external requests.
- 15 competitor tests covering explicit relations, joint-venture owners, business contexts, repeated statements, generic and negative disclosures, shareholder-return peers, ambiguous identities, legal-name punctuation, multiple share classes, missing directory data, Unicode evidence offsets, customer/supplier lists inside competition sections, mixed relations within one paragraph, later direct predicates, and the required competition introduction for contextual business lists.

These automated tests use synthetic fixtures; the real collection described above was checked separately. JavaScript syntax checking passed.

## Browser checks

- The English workspace loaded against the real local backend.
- Clicking Collect filings completed successfully and reported 0 processed, 2 reused, 0 failed.
- Both saved reports appeared with separate filing dates and financial-period dates.
- The annual and quarterly extracted text could be previewed.
- Source and download links pointed to the selected filing.
- Layout checks at 360 px and 1120 px found no page-level horizontal overflow. At small widths, the filing table has its own horizontal scrolling.

## Full-report preview regression

The former preview returned only the first 30,000 characters. In Rocket Lab's (RKLB, CIK 0001819994) 2025 10-K, the saved text contains 431,849 characters and the Competition heading starts around character 56,682. The extractor had preserved the section; the preview had excluded it.

The preview now returns the complete saved text. Its live local response was compared with the saved RKLB text and matched exactly, with no new SEC request. A synthetic HTTP regression test verifies that a section beyond 30,000 characters and the end of a report are included, while text downloads still return the full file.

Browser checks confirmed that Jump to Competition exposes the heading and the disclosed competitors in the real RKLB annual report. Full-report search found SpaceX, previous/next navigation worked, literal punctuation was searchable, and an absent phrase showed a no-results state. Switching to the quarterly report cleared the search and highlight. The shortcut is offered only when a standalone Competition heading is present; it does not perform competitor extraction.

## Disclosed-competitor extraction

The six primary reports in the following matrix were checked through the live local text-preview endpoint. Every returned evidence quote matched its saved text range, including the UTF-16 offsets used for browser highlighting.

| Company and report | Named candidates | Source contexts | SEC directory name matches |
| --- | ---: | ---: | ---: |
| RKLB 2025 10-K | 24 | 3 | 6 |
| RKLB June 2026 10-Q | 0 | 0 | 0 |
| MU September 2026 10-K | 6 | 1 | 2 |
| MU May 2026 10-Q | 6 | 1 | 2 |
| SPGI 2025 10-K | 0 | 0 | 0 |
| SPGI June 2026 10-Q | 0 | 0 | 0 |

RKLB's names come from three explicit lists covering launch vehicles, spacecraft solutions and spacecraft components. Northrop Grumman is retained once with evidence in two contexts. Parent companies mentioned solely as owners of a joint venture are excluded from that list. MU's names come from an explicit competition statement about semiconductor memory and storage; repeated copies of that statement in its annual report are merged. SPGI's shareholder-return peer group is not treated as a list of competitors. Zero extracted names does not establish that a company has no competitors.

Browser checks confirmed automatic results for MU and RKLB, expansion from six visible cards to all 24 RKLB candidates, business-context filtering, switching between Northrop Grumman's two source contexts, inline name highlighting, and jumping to SK hynix's exact disclosure passage. RKLB's quarterly report displayed the empty state with a working action to open its saved annual report. Ticker identities are matched only from cached SEC directory names; unresolved names remain explicitly marked for review.

## Relation-rule correction

A review reproduced two false-positive cases in the original baseline: a customer list inside a Competition section, and a direct competition statement followed by a separate supplier list in the same paragraph. The section-plus-enumeration rule was too broad, and scanning forward for another list marker could select the unrelated list.

The revised rules require a direct competition predicate or a business list immediately covered by an explicit competition-list introduction. They bind list markers to the competition clause and stop at a change to customer, supplier or partner relations. New regression cases cover these failures, legal-name abbreviations at sentence boundaries, unframed business lists, and preservation of the original competition introduction. The UI now distinguishes direct statements from introduced competition lists; SEC directory matching remains a separate identity-spelling check.

An additional Oracle annual report was checked during this review. The supported direct statement names five healthcare IT providers, including Epic Systems, and the evidence is limited to the relevant sentence. The separate statement about Oracle's cloud, software and hardware offerings uses another grammatical form and is not covered by these rules. This is a known recall limitation; the returned candidates are not a complete Oracle competitor list.

## Public snapshot deployment

Published version 1 on October 14, 2026: [Azalea research preview](https://azalea-peer-intelligence-preview.lively-finch-8041.chatgpt.site). The explicitly selected audience is public, with no viewer allowlist or login requirement.

The snapshot contains all 8 ready real reports for SPGI, MU, ORCL and RKLB. Before publication, each exported full text matched its saved bytes and SHA-256 metadata, and each evidence quote matched its source range. The exported bundle was checked for local contact configuration, filesystem paths and non-public file types. It contains no environment files, sessions, database or HTTP cache.

After publication, unauthenticated HTTP requests verified the homepage, library and all eight text, metadata and preview resources. Downloaded text hashes matched metadata, preview text matched downloads, and UTF-16 evidence offsets reproduced each exact quote. The hosting layer rejects the default Python HTTP user agent with Cloudflare error 1010; the successful anonymous check used a browser-compatible user agent and no cookie or authorization header.

Browser checks verified the hosted RKLB results, business-context filter and SpaceX evidence. The local snapshot also passed company switching to Oracle and the evidence-to-full-report jump for Epic Systems. All 69 existing automated checks passed. Online collection, financial peer ranking and automatic snapshot refresh are outside this deployment.

## Limits

The exported-data checks cover four companies and eight primary HTML documents, with the full-preview regression checked against the RKLB annual report. They do not establish all-company coverage, correct extraction for every SEC document format, structured financial-table accuracy, or uninterrupted long-term operation. The HTML extraction is basic and should be checked against original documents before downstream analysis. Loading complete text has been checked on these reports, rather than on every possible document size.

The competitor rules cover selected explicit-list formats. They have not been benchmarked for market-wide precision or recall, may miss aliases and unsupported wording, and do not determine current ownership, listing status, or financial comparability. Names and relationships describe the source filing's date. Industry-wide discovery, business-profile similarity, peer ranking, earnings call transcripts, comparison generation, and scheduled updating remain outside this milestone.
