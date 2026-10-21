# azalea-peer-intelligence
NYU MSDS capstone: Project Azalea — peer identification and peer-relative comparison using public data.

## TNIC baseline

Reproducible TNIC import, audit, top-k peer index and local explorer: [baseline guide](baseline/README.md). This is a descriptive baseline; downstream predictive evaluation awaits historical identifiers, filing dates and financials.

## EDGAR filings workspace

SEC filings collection, report reader, and disclosed competitor evidence are included in this repository. See [EDGAR guide](docs/edgar.md) and [validation notes](VALIDATION.md).

## UI source files

- Peer explorer: `baseline/explorer.html` (layout, styles, and interactions).
- EDGAR reader: `azalea_edgar/static/index.html`, `styles.css`, and `app.js`.

Run commands and make future changes from this repository root. Local `data/`, `outputs/`, and `.env.local` are excluded from Git.
