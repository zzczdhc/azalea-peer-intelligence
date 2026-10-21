'use strict';
const $ = id => document.getElementById(id);
const state = { library: { companies: [], filings: [], last_run: null }, cik: '', selected: null, busy: false, previewRequest: 0,
  documentText: '', matches: [], matchIndex: -1, analysis: null, currentRecord: null,
  competitorContext: '', showAllCompetitors: false, evidenceCandidate: null, evidenceGroup: null };

async function api(path, options) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function secLink(url, label, className) {
  const anchor = element('a', label, className);
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:' && ['www.sec.gov', 'data.sec.gov'].includes(parsed.hostname)) anchor.href = parsed.href;
  } catch { /* Keep invalid source references non-clickable. */ }
  anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
  return anchor;
}
function status(message, mode = '') {
  $('status-message').textContent = message;
  $('collection-status').dataset.state = mode;
}
function error(message) {
  $('collection-error').textContent = message || '';
  $('collection-error').hidden = !message;
}
function setBusy(busy) {
  state.busy = busy;
  $('collect-button').disabled = busy;
  $('collect-button').textContent = busy ? 'Collecting…' : 'Collect filings →';
  $('collection-progress').hidden = !busy;
  $('collection-form').setAttribute('aria-busy', String(busy));
  $('collection-form').querySelectorAll('input,select').forEach(input => { input.disabled = busy; });
}
function reportStatus(report) {
  if (!report) return;
  const summary = `${report.processed} processed · ${report.already_ready} reused · ${report.failed} failed`;
  const names = { complete: 'Collection complete', partial: 'Collection partially complete', blocked: 'SEC access blocked' };
  status(`${names[report.status] || 'Collection finished'}. ${summary}.`, report.status);
  const errors = (report.errors || []).map(item => item.error);
  for (const company of report.companies || []) {
    if (company.status === 'failed' && company.error) errors.push(company.error);
  }
  error([...new Set(errors)].join(' '));
}
async function loadLibrary(preferredTarget) {
  state.library = await api('/api/library');
  const companies = state.library.companies;
  if (preferredTarget) {
    const found = companies.find(c => c.cik === preferredTarget.padStart(10, '0') || c.ticker === preferredTarget || (c.tickers || []).includes(preferredTarget));
    if (found) state.cik = found.cik;
  }
  if (!companies.some(c => c.cik === state.cik)) state.cik = companies[0]?.cik || '';
  const select = $('company-filter'); select.replaceChildren();
  companies.forEach(c => { const option = element('option', `${c.ticker} · ${c.name}`); option.value = c.cik; select.append(option); });
  select.value = state.cik;
  $('company-filter-label').hidden = companies.length < 2;
  renderLibrary();
  const report = state.library.last_run;
  $('last-run-label').textContent = report ? `Last collection: ${new Date(report.finished_at).toLocaleString('en-US')}` : 'No collection yet';
}
function renderLibrary() {
  const company = state.library.companies.find(c => c.cik === state.cik);
  const records = state.library.filings.filter(r => r.cik === state.cik);
  $('filing-count').textContent = records.length;
  $('company-strip').hidden = !company;
  $('empty-state').hidden = records.length > 0;
  $('filings-table-wrap').hidden = records.length === 0;
  $('reader-section').hidden = true;
  $('competitor-section').hidden = true;
  if (company) {
    $('company-symbol').textContent = company.ticker;
    $('company-name').textContent = company.name;
    $('company-details').textContent = [company.sic_description || 'Industry classification unavailable', ...(company.exchanges || [])].join(' · ');
    $('company-cik').textContent = `CIK ${company.cik}`;
  }
  const ready = records.filter(r => r.status === 'ready').length;
  $('library-caption').textContent = records.length ? `${ready} ready report${ready === 1 ? '' : 's'} · Filing date and financial period are shown separately.` : 'Reports saved in this workspace.';
  const tbody = $('filings-body'); tbody.replaceChildren();
  for (const record of records) {
    const row = element('tr'); row.dataset.accession = record.accession;
    const reportCell = element('td'); reportCell.append(element('div', record.form, 'report-type'));
    reportCell.append(element('div', record.form === '10-K' ? 'Annual report' : 'Quarterly report', 'report-caption'));
    row.append(reportCell, element('td', record.filing_date), element('td', record.report_date || 'Not provided'));
    const statusCell = element('td');
    const pill = element('span', record.status === 'ready' ? 'Ready' : record.status === 'failed' ? 'Failed' : 'Pending', `status-pill ${record.status === 'ready' ? '' : record.status === 'failed' ? 'failed' : 'pending'}`);
    statusCell.append(pill); row.append(statusCell);
    const actions = element('td'); const links = element('div', undefined, 'row-actions');
    if (record.status === 'ready') {
      const preview = element('button', 'Preview text', 'preview-button'); preview.type = 'button';
      preview.setAttribute('aria-label', `Preview ${record.form} filed ${record.filing_date}`);
      preview.addEventListener('click', () => showDocument(record)); links.append(preview);
    }
    links.append(secLink(record.source_url, 'SEC ↗', 'source-link')); actions.append(links); row.append(actions); tbody.append(row);
  }
  if (!state.busy) {
    const selected = records.find(r => r.accession === state.selected && r.status === 'ready') ||
      records.find(r => r.status === 'ready' && r.form === '10-K') || records.find(r => r.status === 'ready');
    if (selected) showDocument(selected);
  }
}
function renderCompetitors(analysis) {
  state.analysis = analysis; state.competitorContext = ''; state.showAllCompetitors = false;
  state.evidenceCandidate = null; state.evidenceGroup = null;
  const found = analysis.candidates.length > 0;
  $('competitor-count').textContent = analysis.summary.candidate_count;
  $('competitor-status').hidden = true;
  $('competitor-results').hidden = !found; $('competitor-empty').hidden = found;
  $('competitor-evidence').hidden = !found;
  const annual = state.library.filings.find(r => r.cik === state.cik && r.status === 'ready' && r.form === '10-K');
  $('competitor-open-annual').hidden = found || !annual || annual.accession === state.selected;
  if (!found) {
    $('competitor-empty-message').textContent = analysis.status === 'no_named_competitors' ?
      'A competition section was found, but these rules did not extract an explicit company list. This does not mean the company has no competitors.' :
      'No supported explicit competition list was found in this report. Review the original or try a saved annual report; other disclosure formats may need a different extraction method.';
    return;
  }
  $('competitor-total').textContent = analysis.summary.candidate_count;
  $('competitor-contexts').textContent = analysis.summary.context_count;
  $('competitor-matched').textContent = analysis.summary.matched_count;
  const select = $('competitor-filter'); select.replaceChildren();
  const all = element('option', `All business contexts · ${analysis.candidates.length} companies`); all.value = ''; select.append(all);
  analysis.groups.forEach(group => { const option = element('option', `${group.label} · ${group.candidate_ids.length}`); option.value = group.id; select.append(option); });
  renderCompetitorCards();
}
function renderCompetitorCards() {
  const analysis = state.analysis;
  const candidates = analysis.candidates.filter(c => !state.competitorContext || c.group_ids.includes(state.competitorContext));
  const visible = state.showAllCompetitors ? candidates : candidates.slice(0, 6);
  if (!visible.some(c => c.id === state.evidenceCandidate)) state.evidenceCandidate = visible[0]?.id;
  const cards = $('competitor-cards'); cards.replaceChildren();
  for (const candidate of visible) {
    const card = element('button', undefined, 'competitor-card'); card.type = 'button';
    card.setAttribute('aria-label', `View evidence for ${candidate.name}`);
    card.dataset.candidate = candidate.id;
    card.setAttribute('aria-pressed', String(candidate.id === state.evidenceCandidate));
    card.append(element('strong', candidate.name, 'competitor-name'));
    const match = candidate.identity.status === 'directory_match' ? candidate.identity.matches[0] : null;
    card.append(element('span', match ? match.tickers.join(' / ') : 'Identity needs review', `identity-label ${match ? 'matched' : ''}`));
    card.append(element('span', candidate.group_ids.map(id => analysis.groups.find(g => g.id === id).label).join(' · '), 'competitor-context'));
    card.append(element('span', 'View evidence →', 'competitor-card-action'));
    card.addEventListener('click', () => {
      state.evidenceCandidate = candidate.id; state.evidenceGroup = null;
      cards.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.candidate === candidate.id)));
      renderCompetitorEvidence(); $('competitor-evidence').scrollIntoView({block:'nearest'});
    });
    cards.append(card);
  }
  $('competitor-visible').textContent = `Showing ${visible.length} of ${candidates.length} named candidates`;
  $('competitor-show-all').hidden = candidates.length <= 6;
  $('competitor-show-all').textContent = state.showAllCompetitors ? 'Show fewer' : `Show all ${candidates.length}`;
  renderCompetitorEvidence();
}
function renderCompetitorEvidence() {
  const candidate = state.analysis.candidates.find(c => c.id === state.evidenceCandidate);
  if (!candidate) { $('competitor-evidence').hidden = true; return; }
  const groups = state.analysis.groups.filter(g => candidate.group_ids.includes(g.id));
  const group = groups.find(g => g.id === state.evidenceGroup) || groups.find(g => g.id === state.competitorContext) || groups[0];
  state.evidenceGroup = group.id; $('competitor-evidence').hidden = false;
  $('evidence-heading').textContent = candidate.name;
  const framed = group.relation_basis === 'framed_competition_list';
  $('evidence-basis').textContent = framed ?
    'Relationship evidence: this list is introduced as a source of competition.' :
    'Relationship evidence: a direct competition statement in the source.';
  $('evidence-relation').hidden = !framed;
  $('evidence-relation').textContent = framed ? group.relation_text : '';
  const contexts = $('evidence-contexts'); contexts.replaceChildren();
  groups.forEach(item => {
    const button = element('button', item.label, 'evidence-context-button'); button.type = 'button';
    button.setAttribute('aria-pressed', String(group.id === item.id));
    button.addEventListener('click', () => { state.evidenceGroup = item.id; renderCompetitorEvidence(); });
    contexts.append(button);
  });
  const at = group.quote.indexOf(candidate.name); const quote = $('evidence-quote');
  if (at < 0) quote.textContent = group.quote;
  else quote.replaceChildren(document.createTextNode(group.quote.slice(0, at)), element('mark', candidate.name), document.createTextNode(group.quote.slice(at + candidate.name.length)));
  const identity = candidate.identity;
  $('evidence-identity').textContent = identity.status === 'directory_match' ?
    `SEC directory name: ${identity.matches[0].name} · CIK ${identity.matches[0].cik}. Name match; review business comparability separately.` :
    identity.status === 'ambiguous' ? 'Several SEC directory issuers share this name spelling. Resolve the identity before comparing financials.' :
    'No exact company-name match in the cached SEC ticker directory. Check aliases, ownership and listing status before collecting financials.';
  const source = secLink(state.currentRecord.source_url, 'SEC original ↗', 'button quiet');
  source.id = 'evidence-source'; $('evidence-source').replaceWith(source);
}
function renderMatch() {
  const match = state.matches[state.matchIndex];
  $('reader-previous').disabled = $('reader-next').disabled = state.matches.length < 2;
  if (!match) {
    $('reader-text').textContent = state.documentText;
    $('reader-find-count').textContent = $('reader-search').value.trim() ? 'No matches in this report.' : 'Search the full report.';
    return;
  }
  const mark = element('mark', state.documentText.slice(match.start, match.start + match.length));
  mark.id = 'reader-match';
  $('reader-text').replaceChildren(document.createTextNode(state.documentText.slice(0, match.start)), mark,
    document.createTextNode(state.documentText.slice(match.start + match.length)));
  $('reader-find-count').textContent = `${state.matchIndex + 1} of ${state.matches.length.toLocaleString('en-US')} matches`;
  mark.scrollIntoView({ block: 'center', inline: 'nearest' });
}
function searchDocument(preferredStart = null) {
  const query = $('reader-search').value.trim();
  state.matches = []; state.matchIndex = -1;
  if (query) {
    const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    for (const match of state.documentText.matchAll(pattern)) state.matches.push({ start: match.index, length: match[0].length });
    if (state.matches.length) state.matchIndex = Math.max(0, state.matches.findIndex(match => match.start === preferredStart));
  }
  renderMatch();
}
function nextMatch(direction) {
  if (!state.matches.length) return;
  state.matchIndex = (state.matchIndex + direction + state.matches.length) % state.matches.length;
  renderMatch();
}
async function showDocument(record) {
  if (state.busy) { error('Wait for the current collection to finish before opening a report.'); return; }
  state.selected = record.accession;
  state.currentRecord = record; state.analysis = null;
  const request = ++state.previewRequest;
  const base = `/documents/${record.cik}/${record.accession}`;
  $('reader-section').hidden = false;
  $('competitor-section').hidden = false;
  $('competitor-count').textContent = '…'; $('competitor-status').hidden = false;
  $('competitor-status').textContent = 'Reading competition statements…';
  $('competitor-results').hidden = $('competitor-empty').hidden = true;
  $('competitor-source').textContent = `${record.ticker || record.company_name || record.cik} · ${record.form} · Period ended ${record.report_date} · Filed ${record.filing_date}`;
  $('reader-heading').textContent = `${record.form} · ${record.company_name || record.ticker || record.cik}`;
  $('reader-text').textContent = 'Loading and verifying saved document…';
  state.documentText = ''; state.matches = []; state.matchIndex = -1;
  $('reader-search').value = ''; $('reader-search').disabled = true;
  $('reader-previous').disabled = $('reader-next').disabled = true;
  $('reader-competition').hidden = true; $('reader-find-count').textContent = 'Loading report…';
  $('reader-error').hidden = true;
  $('reader-meta').replaceChildren(); $('reader-footer').textContent = '';
  $('reader-download').href = `${base}/text?download=1`;
  $('reader-metadata').href = `${base}/metadata?download=1`;
  const source = secLink(record.source_url, 'SEC original ↗', 'button quiet');
  source.id = 'reader-source'; $('reader-source').replaceWith(source);
  $('filings-body').querySelectorAll('tr').forEach(row => row.classList.toggle('is-selected', row.dataset.accession === record.accession));
  try {
    const result = await api(`${base}/text`);
    if (request !== state.previewRequest) return;
    const meta = result.metadata;
    const parts = [`Filed ${meta.filing_date}`, `Period ended ${meta.report_date}`, `${meta.words.toLocaleString('en-US')} words`, 'File integrity checked'];
    if (meta.data_kind === 'synthetic_fixture') parts.push('SYNTHETIC TEST DATA');
    if ((meta.warnings || []).length) parts.push(...meta.warnings);
    parts.forEach(part => $('reader-meta').append(element('span', part)));
    state.documentText = result.text;
    $('reader-text').textContent = result.text; $('reader-text').scrollTop = 0;
    $('reader-search').disabled = false;
    $('reader-competition').hidden = !/^[\t ]*Competition[\t ]*$/mi.test(result.text);
    $('reader-find-count').textContent = 'Search the full report.';
    $('reader-footer').textContent = `Full document · ${result.text.length.toLocaleString('en-US')} characters · Collected ${new Date(meta.fetched_at).toLocaleString('en-US')}.`;
    renderCompetitors(result.competitors);
  } catch (err) {
    if (request !== state.previewRequest) return;
    $('reader-text').textContent = '';
    $('reader-find-count').textContent = 'Report unavailable.';
    $('competitor-count').textContent = '';
    $('competitor-status').textContent = 'The saved report could not be analyzed. See the document error below.';
    $('reader-error').textContent = err.message;
    $('reader-error').hidden = false;
  }
}
async function pollJob(id) {
  try {
    const job = await api(`/api/jobs/${id}`);
    if (job.state === 'running') {
      status(`Collecting ${job.target}. Checking SEC records and processing selected reports…`, 'running');
      setTimeout(() => pollJob(id), 1000); return;
    }
    setBusy(false);
    if (job.report) reportStatus(job.report);
    else { status('Collection could not finish.', 'failed'); error(job.error || 'Unknown collection error.'); }
    await loadLibrary(job.target);
  } catch (err) {
    setBusy(false); status('Connection interrupted. Refresh the workspace to check the saved result.', 'failed'); error(err.message);
  }
}
$('collection-form').addEventListener('submit', async event => {
  event.preventDefault(); error('');
  const payload = { ticker: $('ticker').value.trim().toUpperCase(), since: $('since').value, until: $('until').value,
    forms: [...document.querySelectorAll('input[name=form]:checked')].map(input => input.value),
    max_per_form: Number($('max-per-form').value), refresh_documents: $('refresh-documents').checked };
  setBusy(true); status('Starting collection…', 'running');
  try {
    const job = await api('/api/collect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    pollJob(job.id);
  } catch (err) { setBusy(false); status('Collection did not start.', 'failed'); error(err.message); }
});
$('company-filter').addEventListener('change', () => { state.cik = $('company-filter').value; state.selected = null; renderLibrary(); });
$('refresh-library').addEventListener('click', async () => { try { await loadLibrary(); } catch (err) { error(err.message); } });
$('reader-search').addEventListener('input', () => searchDocument());
$('reader-search').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); nextMatch(event.shiftKey ? -1 : 1); }
});
$('reader-previous').addEventListener('click', () => nextMatch(-1));
$('reader-next').addEventListener('click', () => nextMatch(1));
$('reader-competition').addEventListener('click', () => {
  const heading = /^[\t ]*Competition[\t ]*$/mi.exec(state.documentText);
  if (!heading) return;
  $('reader-search').value = 'Competition';
  searchDocument(heading.index + heading[0].toLowerCase().indexOf('competition'));
});
$('competitor-filter').addEventListener('change', () => {
  state.competitorContext = $('competitor-filter').value; state.showAllCompetitors = false;
  state.evidenceCandidate = null; state.evidenceGroup = null; renderCompetitorCards();
});
$('competitor-show-all').addEventListener('click', () => { state.showAllCompetitors = !state.showAllCompetitors; renderCompetitorCards(); });
$('competitor-open-annual').addEventListener('click', () => {
  const annual = state.library.filings.find(r => r.cik === state.cik && r.status === 'ready' && r.form === '10-K');
  if (annual) showDocument(annual);
});
$('evidence-open').addEventListener('click', () => {
  const candidate = state.analysis.candidates.find(c => c.id === state.evidenceCandidate);
  const group = state.analysis.groups.find(g => g.id === state.evidenceGroup);
  if (!candidate || !group) return;
  const at = group.quote.indexOf(candidate.name);
  if (at >= 0) { $('reader-search').value = candidate.name; searchDocument(group.start_utf16 + at); }
  else {
    $('reader-search').value = ''; state.matches = [{start:group.start_utf16,length:group.end_utf16 - group.start_utf16}];
    state.matchIndex = 0; renderMatch(); $('reader-find-count').textContent = 'Source evidence highlighted.';
  }
});
async function init() {
  try {
    const config = await api('/api/config');
    $('since').value = config.since; $('until').value = config.until;
    $('since').max = config.until; $('until').max = config.until;
    await loadLibrary();
    if (config.active_job) { setBusy(true); pollJob(config.active_job); }
    else if (!config.contact_configured) { status('A project contact is required before collecting from SEC.'); }
    else if (state.library.last_run) reportStatus(state.library.last_run);
    else status('Ready to collect. Existing valid files will be reused.');
  } catch (err) { status('Workspace unavailable.', 'failed'); error(err.message); }
}
init();
