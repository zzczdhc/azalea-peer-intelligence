# TNIC baseline implementation

This first release imports the downloaded TNIC-3 peer network, audits the complete input, and creates a reproducible top-10 query index. It does not reproduce the TNIC text algorithm or establish predictive superiority. The official scores already exist; the research work is using these peers in a fair downstream experiment.

## Build and explore

Run from the repository root using Python 3 (standard library only):

```sh
python3 baseline/tnic.py --source /Users/wushuang/Downloads/tnic3_data.zip --output outputs/tnic-baseline
python3 baseline/serve.py --data outputs/tnic-baseline --port 8766
```

Open http://127.0.0.1:8766. Output directories must be new to avoid accidental replacement. To examine another k, rebuild into a new directory with `--k 5` or `--k 20`. The page renders whatever k the index contains, the default index uses k=10.

Generated files:

- `audit.json`: source path, source ZIP SHA-256, selection rule, yearly audit and explicit descriptive-only status.
- `yearly.csv`: yearly coverage, degree statistics, self rows, mirror-score consistency and insufficient-peer counts.
- `top_peers.csv`: fiscal year, focal GVKEY, rank, peer GVKEY and original supplied TNIC score.
- `tnic.sqlite`: indexed peer lists and counts for all focal firm-years.

All data outputs stay local and are excluded from Git. Never commit source archives or credentials.

## Selection and audit rules

1. Require columns `year, gvkey1, gvkey2, score` and partition rows by year on disk (the source can append older-year self records).
2. Reject duplicate directed firm pairs within year, non-finite scores and negative scores. Reading the full ZIP member verifies its CRC.
Blank scores are counted explicitly and excluded from ranked lists, while their relationships remain in original peer counts. They are never replaced with zero.

3. Count and exclude self records from selection. Preserve both directions of a pair for focal-company aggregation.
4. Rank by descending supplied score, breaking ties by ascending peer GVKEY. Do not interpret the score as raw cosine similarity.
5. Keep at most k peers from the original TNIC threshold network. If fewer exist, keep fewer; do not manufacture matches.
6. Check every supplied pair for a reverse pair with the same score. Count discrepancies rather than silently repairing the source.

The query view supports GVKEY, company-name and ticker search. Company names and ticker labels are static display metadata, not verified point-in-time research links. Annual coverage is descriptive, not a prediction score. The interface does not invent text evidence.

## Definition of a completed Direction 4 baseline

The current release completes peer-data ingestion and descriptive inspection. A research baseline additionally requires:

- Historical CIK–GVKEY mapping and accession–CIK metadata with actual filing dates, fiscal dates, form types and contemporaneous SIC where available.
- Financial observations with original availability dates; avoid later restatements being silently substituted into historical predictors.
- A declared target and horizon (proposed first target: next-year ROA), with documented accounting definitions, units, denominators, missing-value and outlier rules.
- A fixed temporal training/validation/test split. Fit scaling, text vocabulary, fusion weights and model hyperparameters on training/validation data only.
- Each observation indexed by an explicit prediction date. TNIC year labels reflect fiscal year, NOT public availability. Do not simply join by year. If source filing availability cannot be verified, keep this baseline descriptive until a documented conservative lag design is validated.
- Peer-relative feature, initially company metric minus the equal-weight mean of available non-self peer metrics. Report missing-peer coverage; predefine minimum coverage and never silently replace missing peers. Test the median as a sensitivity analysis.
- Same target observations, predictors, model family and tuning budget for raw financial features, raw+SIC-relative, raw+TNIC-relative, and raw+our-peer-relative versions. Use the common eligible sample and report excluded coverage separately.
- Primary metric proposed: held-out MAE. Report RMSE, out-of-sample R-squared with its benchmark explicitly defined, paired error differences, and dependence-aware confidence intervals (e.g. company/time block resampling justified for the panel).
- Include native TNIC threshold peers with equal weighting in downstream aggregation, alongside matched top-k variants. The SQLite index stores top-k; native all-peer aggregation must stream the source archive, not mistake the index for the full network.
- Preselect outcomes and report all planned comparisons, year stability, k sensitivity, size controls, and text-only/fundamentals-only ablations. Do not select the best test-set result.

No fixed percentage improvement automatically means a win. Claim only task-specific improvement supported by held-out paired comparisons, uncertainty and meaningful effect size. Do not compare numerical similarity-score scales across methods.

## Checks

```sh
python3 -m unittest discover -s tests -v
```

Tests cover self exclusion, deterministic ties, valid zero scores, no backfill, duplicate rejection, invalid score rejection and unordered source years. They validate data mechanics, not predictive validity.

## Data provenance

- TNIC-3 official data library: https://hobergphillips.tuck.dartmouth.edu/industryclass.htm
- Cite Hoberg and Phillips (2010), Review of Financial Studies, and Hoberg and Phillips (2016), Journal of Political Economy, as requested in the supplied README.
- The downloaded ZIP includes `Readme_tnic3.txt`; it is the authority for score interpretation, self rows and fiscal-year labels.

## Optional company display names

`baseline/names.py` attaches names without changing TNIC scores or ranks. It reads `cik_gvkey.pq` and `cik_ticker.pq` snapshots from https://github.com/Wenzhi-Ding/Std_Security_Code/tree/main/other. The source author describes these mappings as suitable for exploration and requiring independent verification before formal research. Source data remains local and is not redistributed in this repository.

The importer requires DuckDB 1.4.3 only; the index builder and viewer remain standard-library Python. Example with the locally installed reader:

```sh
python3 baseline/names.py --source outputs/name-mapping --data outputs/tnic-baseline --runtime outputs/name-mapping/runtime
```

Only rows explicitly marked `Valid CIK-GVKEY Link` in the source are candidates. Compustat Company labels take precedence. If that preferred group identifies conflicting CIKs or different normalized company names, the GVKEY remains unresolved; no fuzzy matching or first-row guessing is used. Normalization only removes punctuation and case. A ticker is the latest observed label for the chosen CIK in the snapshot; equally recent labels are all displayed. These are not claimed to be the tickers at the selected TNIC year.

`names_audit.json` records source hashes, selection policy, unresolved ambiguous identifiers and year-by-year name coverage. `/api/search` searches only companies actually present in the selected TNIC year. `/api/peers` retains numeric GVKEYs and additionally returns company metadata and peer labels. Unmapped companies remain queryable by GVKEY.

A verified historical identity master is still required before these display labels can serve as research joins. The mapping is never used as a predictive feature or to modify peer selection.

## SIC descriptive baseline

The SIC view imports the SEC Financial Statement Data Sets' `sub.txt` from all four 2023 quarterly archives. These contain actual SIC codes, filing accession numbers, filing dates and report periods. They are not inferred from company names or today's industry codes.

```sh
python3 baseline/sic.py --source outputs/sic-source --data outputs/tnic-baseline
```

Each query selects the latest eligible annual/quarterly filing observed within the selected calendar year, on or before December 31 (including amendments). It joins through the static display CIK mapping, restricts to companies in that year's TNIC universe, and excludes CIKs linked to multiple GVKEYs within that universe. If a year or company is unavailable, the API returns an explicit unavailable state rather than substituting a later code. This remains exploratory because the identity mapping and TNIC year labels have not been verified for point-in-time backtesting.

Two- and three-digit groups are the guidance's required SIC baselines and the only supported choices. Matching uses the prefix of the zero-padded four-digit SIC; the focal company is excluded. All group members have equal membership, no similarity score or relevance rank. Pagination is alphabetical. Coverage and counts reflect the eligible linked TNIC universe, not every public company in the SEC archive. Overlap is against the displayed TNIC top-k set and explicitly states how many of those peers have SIC observations; it is not a performance score.

`/api/sic?year=2023&gvkey=7343&digits=3&offset=0` returns observed SIC, source filing links, group counts, eligible-universe coverage, 25 alphabetical peers per page and displayed-TNIC overlap. `sic_audit.json` records source URLs, SHA-256 hashes, loaded filing years and import rules. Original ZIPs and all generated data remain ignored by Git.

## Pipeline and commercial context

The English workspace has three views:

- Peer explorer: real TNIC rankings and real SIC membership groups in a shared tabbed panel.
- Section 4 pipeline: the guidance's common representation, late-fusion, selection and baseline workflow; computed and pending steps are explicitly separated. This is not the same as research Direction 4.
- Commercial use and evaluation: the proposed existing-customer-to-prospect workflow, evidence requirements and evaluation options. It contains no customer purchase data, inferred product demand, fabricated model outputs or sales-conversion results.

For sales use, similarity is a candidate discovery signal. Validating product needs requires a product catalogue and independent business review or permitted outcome labels. Blinded judged relevance can support Precision@k or nDCG@k only after a label set and rubric exist. Direction 4's financial predictive tests remain a separate research track; they cannot stand in for sales effectiveness.

## Peer-set intersection view

TNIC and SIC share one tabbed panel. The only SIC grouping choices are two and three digits. A schematic Venn diagram below the panel compares the displayed TNIC top-k set with the entire eligible SIC group, independent of pagination. It shows the intersection and each exclusive region, with the full shared-company list available for exploration. Circle area is explicitly not proportional to counts; disjoint and containment cases use different layouts. Missing SIC coverage is stated so absence from an observed SIC set is not mistaken for a definitive industry mismatch.

The SIC API includes `overlap_peers` for the entire group, even if the shared company is not on the current alphabetic page. Changing company, year or SIC grouping resets the comparison while new data loads; unavailable years show an explicit empty state.

## Independent business-text heatmap

The heatmap displays **every SIC group member**, independent of table pagination, scored against the selected company using one common TF-IDF cosine model. A `T` marker identifies peers in the displayed TNIC top-k list. The same 0–1 color scale applies to every tile. Missing text is displayed as N/A and excluded from group means, never replaced by zero. Summaries compare TNIC's displayed list, the entire SIC group, and SIC members not in the displayed TNIC list. These are overlapping selection sets, not randomized evaluation samples.

```sh
python3 baseline/text_similarity.py --source /Users/wushuang/Downloads/10-K_item1.zip --data outputs/tnic-baseline --year 2023
```

The build matches the latest non-amended 10-K filed in the selected calendar year to the downloaded Item 1 text **by exact accession number**, rather than trusting the ZIP folder's year label. It restricts to unambiguous CIKs in the TNIC-year universe. Documents require 500 characters and 100 remaining tokens. The explicit tokenizer and stop list are in `text_similarity.py`; retained unigrams appear in at least two and at most 80% of corpus documents. Weight = `(1 + ln count) * (ln((1+N)/(1+df)) + 1)`, followed by L2 normalization. Cosine is a sparse dot product.

`text_vectors` and `text_terms` store vectors and vocabulary in the local index; `text_audit_2023.json` records corpus counts, source ZIP hash, preprocessing, exclusions and descriptive status. The 2023 build matched 3,889 annual Business descriptions. This is a descriptive lexical diagnostic, not a trained/held-out peer-quality or sales-prediction experiment. Static CIK labels still require verification for formal historical research. Word overlap is related to TNIC's own selection criterion, so an observed higher mean cannot establish superior downstream performance.

`/api/heatmap?year=2023&gvkey=7343&digits=3` returns all SIC members plus displayed TNIC members on the same score scale, including text filing dates and score coverage. This metric is explicitly separate from supplied TNIC scores. Classic TNIC-3 uses filtered binary word-presence vectors and cosine, not a modern trained semantic encoder; newer embedding-based TNIC datasets are distinct.

## Financial-only nearest neighbours

The third official baseline is implemented for the 2023 filing cohort using the previously downloaded SEC quarterly `num.txt` archives. Build:

```sh
python3 baseline/fundamentals.py --source outputs/sic-source --data outputs/tnic-baseline --year 2023
```

Five equal-weight financial features: log assets (size), net income / annual revenue (margin), annual revenue growth using the same revenue tag and the previous annual period in the same filing, liabilities / assets (leverage proxy, not debt / assets), and net PPE / assets (capital intensity). The implementation retains exact tags and raw amounts. It uses only unsegmented, non-coregistrant USD US-GAAP facts; balance-sheet facts have qtrs=0 and income facts qtrs=4 at the selected annual report date. Conflicting duplicate values are treated as missing. Liabilities may sum reported current and noncurrent components. If plain net PPE is absent, the standard combined net PPE and finance-lease ROU asset tag is used and traced. Partial goods-only or services-only revenue tags are not treated as total revenue.

The latest non-amended annual filing observed in the calendar year is used for each unambiguously mapped TNIC company. Revenue growth requires a positive same-tag annual comparison ending 300–430 days before the current annual period. Positive assets and current revenues, nonnegative liabilities/PPE and all five finite features are required. Missing features are not filled with zero or imputed. The initial build yields 2,499 complete profiles from 3,892 eligible annual filings; 1,393 are excluded with reasons in `financial_exclusions`. Fiscal periods differ and the static display mapping still limits historical research validity.

Each feature is clipped to the cohort's 1st/99th percentiles, then standardized with the cohort population mean and standard deviation. Euclidean distance across the five z-scores defines nearest neighbours. Self is excluded, ties use GVKEY, and ten results are shown without an industry filter. Distances are not cosine similarities or probabilities. The interface displays raw pre-clipping values and source filing links. This is a descriptive financial peer baseline, not a trained / held-out prediction experiment; research evaluation must fit preprocessing only on the permitted training information.

`financial_profiles`, `financial_models` and `financial_exclusions` are stored locally. `financial_audit_2023.json` records feature definitions, input ZIP hashes, scaling parameters and exclusion counts.

## Selectable two-to-four-method overlap

Users can select TNIC, full SIC, financial top 10 and TF-IDF top 10. Single embedding remains unbuilt and is explicitly not substituted with TF-IDF. TF-IDF ranks over the entire eligible text corpus, not only companies in the selected SIC group. Query rankings are cached against the index modification time.

Two selected sets use schematic Venn circles. Three or four selected sets use three or four schematic circles, with exact-intersection buttons for all 7 or 15 membership combinations (including zero counts). Three-circle regions show exact counts; four circles show the all-selected count and cannot geometrically represent every possible combination. The buttons preserve every exact combination. Each company belongs to exactly one combination, and these counts sum to the union. Click an intersection to inspect the complete company list for that exact combination. All-selected intersection and union sizes are shown. An unavailable baseline returns an explicit unavailable state rather than being silently treated as an empty peer set. These overlaps compare observed sets with different eligible coverage, not performance scores.

Endpoints: `/api/financial`, `/api/tfidf`, and `/api/overlap?year=2023&gvkey=1690&digits=2&methods=tnic,sic,financial,tfidf`.

## On-demand financial years

Selecting a company calls the local POST /api/financial/prepare endpoint. Completed years are read from SQLite. Missing complete calendar years (2010 onward, within the indexed TNIC universe) are queued in one serialized background worker. The browser polls progress and ignores results for superseded selections. Four SEC quarterly archives are downloaded sequentially to outputs/sic-source with temporary-file validation and reuse of completed archives. Filing imports upsert accessions and preserve other years; the existing financial builder computes and commits that year's cohort. Changing the selection does not cancel the shared annual job.

Set SEC_USER_AGENT to an identifying application/contact string before starting the server. SEC may deny downloads with HTTP 403; failures are displayed explicitly and selecting the company again retries. Alternatively, place the four official YYYYq1.zip through YYYYq4.zip archives in outputs/sic-source. No alternate-year financials are substituted. The population still uses linked TNIC-year companies with complete financial features, not every SEC filer. This feature does not prepare historical TF-IDF data. The existing descriptive calendar-year filing convention remains unchanged.
