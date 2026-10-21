# Azalea Peer Intelligence

NYU MSDS capstone: company peer identification and peer-relative comparison using public data, for Project Azalea with S&P Global Ratings.

The local peer explorer lets you select a company and year, inspect several independent peer definitions, and combine section-level text and financial signals through weighted late fusion. It is a working **descriptive research prototype**. It has not yet demonstrated that its recommendations outperform TNIC or improve a downstream business outcome.

## What is implemented

| Guidance item | Current implementation | Remaining work |
| --- | --- | --- |
| 4.1 — Sparse text | Independent annual TF-IDF for Business, Risk Factors and MD&A | Broader text-quality review, boilerplate controls and temporal fitting for predictive experiments |
| 4.1 — Fundamentals | Five financial features, within-year standardization and nearest neighbours | Review excluded companies and verify historical identifier links |
| 4.1 — Dense embeddings | Not implemented | General-purpose versus finance-tuned model comparison |
| 4.1 — LLM summaries then embeddings | Deferred | Source-grounded summaries and look-ahead assessment if introduced |
| 4.2 — Separate scores and weighted fusion | Raw scores, percentile calibration, adjustable weights and contribution breakdown | Validation-based weight selection |
| 4.3 — Peer selection | Fixed Top 10, excluding the focal company | Sensitivity to k, thresholds and size restrictions |
| 4.4 — Baselines | Official TNIC, SIC 2/3-digit groups, financial nearest neighbours and section TF-IDF | Single semantic embedding without fusion; held-out performance comparisons |

The Shared pipeline tab contains the detailed completion checklist. Implemented features are distinguished from evaluated research claims. LLM summaries are optional in the current implementation; TF-IDF does not substitute for the planned semantic embedding baseline.

## Repository workspaces

| Workspace | Purpose | Launch | Default address |
| --- | --- | --- | --- |
| Peer explorer | TNIC, SIC, Financial, section TF-IDF, Fusion and overlap | `python3 start.py` | http://127.0.0.1:8766/ |
| EDGAR reader | Collect and inspect filings and disclosed-competitor candidate evidence | `python3 -m azalea_edgar.web --port 59942` | http://127.0.0.1:59942/ |

These are separate local applications. Collecting a report in the EDGAR reader does not automatically add a parsed section to the peer explorer's annual text corpus. The reader's rule-based competitor evidence is not a validated peer-similarity label. See the [EDGAR guide](docs/edgar.md) and [validation notes](VALIDATION.md).

## Environment and installation

Run all commands from the repository root. Python **3.11 or newer is recommended for the complete repository**, matching `pyproject.toml`. Baseline modules also work on Python 3.9+, but that is not the declared requirement of the EDGAR package.

```sh
git clone https://github.com/zzczdhc/azalea-peer-intelligence.git
cd azalea-peer-intelligence
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install duckdb==1.4.3
```

On Windows, activate with `.venv\Scripts\activate` and use your Python launcher as appropriate. DuckDB is required for the one-time Parquet identifier import only. The peer server, TF-IDF and financial calculations use the Python standard library. They do not require an embedding API, GPU or model download.

Cloning the repository **does not download the source datasets or include a prepared database**. Obtain the team-approved source snapshots before expecting the explorer to reproduce a teammate's results.

## Required data

| Input | Used for | How to obtain or prepare |
| --- | --- | --- |
| `tnic3_data.zip` | Official TNIC scores, yearly research universe and displayed Top 10 | Download from the [Hoberg–Phillips data library](https://hobergphillips.tuck.dartmouth.edu/industryclass.htm); retain the included README and citation requirements |
| `cik_gvkey.pq`, `cik_ticker.pq` | Link GVKEY to SEC CIK and display company names/tickers | Obtain matching snapshots from [Std_Security_Code](https://github.com/Wenzhi-Ding/Std_Security_Code/tree/main/other) |
| 10-K Item 1 text | Business TF-IDF | Team-approved pre-parsed Business corpus |
| 10-K Item 1A text | Risk TF-IDF | Team-approved **annual** Risk Factors corpus |
| 10-K Item 7 text | MD&A TF-IDF | Team-approved **annual** MD&A corpus |
| Four quarterly SEC financial archives per year | Filing metadata, SIC and financial facts | Downloaded automatically on demand; manual placement is also supported |

Text input may be a ZIP or an extracted directory. Each `.txt` filename must have the SEC filing accession as its stem, such as `0000123456-23-000001.txt`. Directories are scanned recursively. Match the accession, not just the company name or a year folder. A fiscal-year directory can contain a filing submitted in a different calendar year; the selected filing date determines the cohort.

Do not substitute complete 10-K documents, annual Business text or quarterly risk updates for another section. The current pipeline uses annual 10-K sections; downloaded 10-Q text is not automatically mixed into these signals. No custom raw-filing section parser runs during peer preparation.

A conventional local layout is:

```text
data/
  10-K/1/<year>/<accession>.txt
  10-K-Risk/1A/<year>/<accession>.txt
  10-K-MDA/7/<year>/<accession>.txt
outputs/
  name-mapping/
    cik_gvkey.pq
    cik_ticker.pq
  sic-source/
    2022q1.zip ... 2022q4.zip
  tnic-baseline/
    tnic.sqlite
    audit.json
    names_audit.json
    financial_audit_<year>.json
    text_audit_<year>.json
    risk_audit_<year>.json
    mda_audit_<year>.json
```

The three conventional text directories are detected when no explicit source is configured. Other locations can be passed to the launcher. `data/` and `outputs/` are ignored by Git.

## First-time setup

### 1. Build the official TNIC index

```sh
python3 baseline/tnic.py --source /path/to/tnic3_data.zip --output outputs/tnic-baseline
```

The builder audits the supplied network and stores up to ten official peers per company/year. It does not reproduce TNIC's text construction. The output database must not already exist; use the existing database or a new output directory rather than overwriting it. Initial indexing can take time and disk space.

The locally imported official archive covers 1988–2023. This is separate from the **2010–2023** range currently offered for automatic annual financial/text preparation. Earlier TNIC years can be inspected without implying that other representations are available.

### 2. Attach identifiers and display labels

Place the two mapping files in `outputs/name-mapping`, then run:

```sh
python3 baseline/names.py --source outputs/name-mapping --data outputs/tnic-baseline
```

Without this mapping, numeric TNIC GVKEY inspection is possible, but company search and SEC-linked calculations are not ready. The importer retains only accepted links and avoids arbitrary choices for ambiguous identifiers. The mapping is an exploratory static snapshot, **not a verified historical identity master**; company names and tickers may reflect later observations.

### 3. Configure sources and launch

For extracted directories:

```sh
python3 start.py --business-text data/10-K/1 --risk-text data/10-K-Risk/1A --mda-text data/10-K-MDA/7 --sec-contact your-email@example.edu
```

For ZIP archives, pass their paths to the same options:

```sh
python3 start.py --business-text /path/to/10-K_item1.zip --risk-text /path/to/10-K_item1A.zip --mda-text /path/to/10-K_item7.zip --sec-contact your-email@example.edu
```

Replace placeholder paths and email with your own. The contact address is sent to SEC as part of the automated request's User-Agent; no email password or SEC API key is needed. Source paths and contact are saved locally under the output directory. They are not committed to Git.

Open http://127.0.0.1:8766/ and keep the terminal running. Subsequent launches normally need only:

```sh
python3 start.py
```

Useful options: `--port 8767` for another port and `--data /path/to/prepared-output` for another prepared database directory. `python3 start.py --help` lists the launcher arguments. Environment variables `BUSINESS_TEXT_ZIP`, `RISK_TEXT_ZIP`, `MDA_TEXT_ZIP` and `SEC_USER_AGENT` override saved source/contact settings. Despite their names, text source environment variables can also point to extracted directories. The peer launcher does not read the EDGAR reader's `.env.local` automatically.

## What happens when a company/year is selected

1. **TNIC:** query the already indexed official network for that company/year.
2. **Financial:** reuse the annual cache, or queue downloads of all four official SEC quarterly ZIPs, import filing metadata and build the annual complete-case feature cohort.
3. **TF-IDF:** prepare the selected section if needed. Wait for the shared annual metadata preparation, match exact filing accessions in the configured local text source, fit that section/year's vocabulary and IDF, then save vectors and the audit.
4. **Fusion:** prepare every positively weighted text signal and the financial signal if active, then combine scores over the common candidate pool.
5. **Results:** display rankings and refresh relevant heatmap/overlap results after preparation.

The first request for an unprepared year may take minutes and download hundreds of MB. Later companies in that year reuse its profiles and vectors. Company-specific text and fusion ranking can still take seconds; it is not a precomputed all-pairs matrix.

Within one server process, duplicate year/section requests reuse an in-flight job and annual builds are serialized. Switching company/year ignores stale UI responses while the shared job continues. Run only one preparation server per output database. The text worker currently reuses the financial-year preparation job for filing metadata, so first-time text preparation can also require financial archive downloads.

Financial source files are downloaded automatically; **missing pre-parsed text corpora are not downloaded automatically**. Missing source, pending preparation, failed calculation and a company with no matched text are different states. Missing data is never replaced with a zero similarity.

## Using the interface

- **TNIC:** official supplied peer scores and ranking. Fewer than ten qualifying official peers remain a shorter list; unrelated companies are not added to fill it.
- **SIC:** choose two- or three-digit grouping. Membership is unranked and the complete eligible group is used for overlap, independent of the visible table page.
- **Financial:** view five raw features, standardized-profile distance, nearest ten and source filings.
- **TF-IDF:** switch Business, Risk Factors or MD&A inside one tab. Each section has its own corpus, vocabulary, IDF and ranking.
- **Fusion:** set nonnegative weights and click **Prepare signals & combine**. The default is 25% each. A zero weight removes that signal; positive weights are normalized to sum to one.
- **Overlap:** select any 2–4 of TNIC, SIC, Financial, the three section TF-IDF sets and Fusion. A fifth set is disabled until another is deselected. Fusion uses the current weight controls; its configuration is printed with the comparison. Changing weights refreshes an overlap that includes Fusion.
- **Circles:** two/three-set diagrams show region counts; four circles are schematic and cannot geometrically represent all 15 exact combinations. Exact-combination buttons preserve the full counts and company lists. Areas are not proportional to company counts.
- **Heatmap:** remains explicitly **Business TF-IDF across SIC peers**, even when another TF-IDF section is selected. Gray/N/A means no matched text, not zero similarity.
- **Shared pipeline:** Section 4 implementation checklist and limitations.
- **Commercial use & evaluation:** proposed business application and evaluation needs, not observed sales-conversion results.

## How scores are calculated

### Section TF-IDF

The latest non-amended 10-K submitted during the selected calendar year is matched to the same accession in each text section. Texts shorter than 500 characters or 100 remaining tokens are excluded. Each section independently uses lowercase ASCII word unigrams of length 3–40 and a fixed English/filing stop list.

For term frequency `tf`, corpus size `N` and document frequency `df`:

```text
TF  = 1 + ln(tf)
IDF = ln((1 + N) / (1 + df)) + 1
term weight = TF × IDF
```

Terms must occur in at least two documents and at most 80% of the corpus. Vectors are L2-normalized; cosine is their dot product. Higher is more similar. Vocabulary and IDF are **fitted from the annual corpus**; no neural network or supervised similarity model is trained.

### Financial distance

The five equally weighted features are log assets, net income/revenue, annual revenue growth, liabilities/assets and net PPE/assets. Revenue growth uses compatible current/prior annual revenue facts. Net PPE has a documented combined finance-lease ROU fallback. Use USD, consolidated US-GAAP annual facts; missing/ambiguous required facts exclude a company rather than being imputed.

Each year's complete-case cohort is winsorized at the 1st/99th percentiles and standardized using the population mean and standard deviation. Euclidean distance measures separation: **lower is closer**. Candidate companies are linked members of the TNIC-year research universe, not only the focal company's TNIC neighbours and not all SEC filers. Detailed accounting rules and exclusions are recorded in the financial audit and [baseline guide](baseline/README.md).

### Weighted late fusion

For a target company, retain candidates with all positively weighted representations. Rank each text cosine and **negative financial distance** within this common pool, then convert to a 0–1 percentile using average ranks for ties. Compute:

```text
combined score = Σ normalized_weight(signal) × percentile(signal)
```

Higher is better under the selected weighting. The table shows original cosine/distance, calibrated percentile, weighted contribution and filing link. Contributions sum to the combined score. They explain the numerical ranking, not a causal business need.

Percentile calibration and equal initial weights are implementation choices; the guidance specifies weighted late fusion but does not prescribe these choices. Removing a signal can change both the candidate pool and its percentiles. Weights have not been optimized on validation data. A high fused score is not a probability, a percentage of business similarity or proof that the method beats a baseline.

### Why TF-IDF and TNIC numbers differ

The official classic TNIC-3 supplied score is the excess above its inclusion threshold, rather than the raw cosine shown by this independent TF-IDF implementation. Text filtering and representation also differ. Do not compare their numerical magnitudes directly or treat a higher overlap count as accuracy. Compare methods using independent judged relevance or a predefined downstream outcome.

## Cache and reproducibility

Computed vectors, profiles, peer index and identifiers are in `outputs/tnic-baseline/tnic.sqlite`. SEC archives are in `outputs/sic-source`; local contact/source configuration is in hidden files under the baseline output directory. Completed annual results survive a restart.

Audit JSONs record source provenance/hashes, formulas, filtering, coverage and exclusions. ZIP text audits hash the complete source archive; extracted-directory text audits hash the sorted matched corpus using GVKEY, accession and text bytes. Reproduce exact ranks using the same code revision, source snapshots, identifier mappings, selected year and fusion weights.

Existing completed models are reused; changing a source path does not automatically invalidate them. To run a clean comparison against changed sources, prepare a separate output directory and index/mapping there, and launch with `--data`. There is no UI rebuild button or schema-version migration system at present.

Git includes code, tests and instructions. It excludes source datasets, generated databases, contact configuration and `.env.local`. Share source locations and snapshot hashes with teammates; do not assume a clone contains the author's local data or disclose another teammate's contact information in committed configuration.

## Checks and troubleshooting

Run the automated checks from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

Tests use local/synthetic fixtures; some start a loopback HTTP server. Real SEC availability and complete source-corpus coverage require separate checks. The examples in the UI use locally built real data, not evidence of predictive superiority.

| Symptom | What to check |
| --- | --- |
| Initial index missing | Build TNIC first, or supply `start.py --tnic-source /path/to/tnic3_data.zip`; identifier import is still required afterward |
| Name search or SEC linkage unavailable | Attach both identifier snapshots and inspect `names_audit.json` |
| Business/Risk/MD&A source missing | Configure the corresponding launcher option or environment variable; check whether the configured path still exists after moving the project |
| SEC HTTP 403 | Check your identifying User-Agent/contact and network access; a valid contact does not guarantee access. Put the four official `YYYYq1.zip` … `YYYYq4.zip` in `outputs/sic-source` if downloading manually |
| Build failed | Read the displayed reason; completed quarterly files are retained. Reselect the company to retry a failed job |
| Company has no matched section text | Check exact accession coverage in that section, file length filtering, mapping and filing dates; do not substitute another year |
| Financial profile unavailable | Review annual exclusion reasons; all five financial features must be computable |
| Fusion unavailable or fewer candidates | An active signal is missing for the target, or common coverage is limited. A zero weight intentionally removes a signal; report the resulting coverage change |
| Port already in use | Stop the earlier local server or launch with `--port 8767` |
| New source gives old results | Cached annual models are reused; use a separate output directory for a clean rebuild |
| Different teammates get different rankings | Compare code revision, raw source hashes, identifier snapshots, year, active signals and weights |

## Research boundaries and next steps

The current calendar-year view uses filings submitted during that year, but TNIC's year is a fiscal-year label and identity mappings are static. Financial period ends differ across companies. This is not yet a uniformly point-in-time predictive dataset.

Before claiming a research result, establish verified historical identifiers and decision dates, frozen temporal train/validation/test splits, training-only transforms, validation-selected weights, a single-embedding baseline, peer-selection sensitivity and a held-out evaluation with uncertainty. Choose and execute a Section 5 research direction; a working peer-finding UI alone is not the intended scientific contribution.

Supplier/customer relationship graphs, typed corporate links and multi-hop contagion are outside this team's peer-comparison scope. No customer purchase history, validated product-fit labels or conversion-lift experiment is included.

## Code map and further documentation

| File | Responsibility |
| --- | --- |
| `start.py` | Local source/contact configuration and peer-server launch |
| `baseline/serve.py` | Local API and HTML serving |
| `baseline/explorer.html` | Peer explorer, pipeline checklist and evaluation UI |
| `baseline/tnic.py` | Official TNIC import, audit and Top 10 index |
| `baseline/names.py` | Identifier/name import from Parquet snapshots |
| `baseline/sic.py`, `baseline/year_jobs.py` | Filing/SIC metadata, SEC annual download and financial-year jobs |
| `baseline/fundamentals.py` | Financial feature construction, normalization and distances |
| `baseline/text_similarity.py`, `baseline/text_jobs.py` | Section-level TF-IDF and on-demand annual jobs |
| `baseline/fusion.py` | Common-pool calibration and weighted late fusion |
| `baseline/overlap.py` | Exact peer-set membership partitions |
| `azalea_edgar/` | Separate SEC document collection/reader workspace |
| `tests/` | Automated checks |

See [baseline details](baseline/README.md), the [EDGAR guide](docs/edgar.md) and [validation notes](VALIDATION.md). Cite the original data and research sources when using their results; importing official TNIC peers is not an independent replication of the original method.
