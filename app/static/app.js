/* Credit Operations Workbench. Reads everything from Camunda through the local API. */
const PERSONAS = [
  { id: 'nomvula', name: 'Nomvula Khumalo', role: 'Dispute analyst, Johannesburg', groups: ['dispute-analysts', 'dispute-specialists'] },
  { id: 'arvind', name: 'Arvind Rao', role: 'Senior dispute analyst, Chennai', groups: ['senior-analysts', 'dispute-analysts', 'dispute-specialists'] },
  { id: 'megan', name: 'Megan Brooks', role: 'Fraud and compliance, Chicago', groups: ['fraud-compliance', 'compliance-officers'] },
  { id: 'karthik', name: 'Karthik Subramanian', role: 'On-call engineer, Pune', groups: ['dispute-platform-oncall', 'data-ingest-oncall', 'infra-oncall', 'sre-oncall', 'service-owners'] },
  { id: 'elena', name: 'Elena Petrova', role: 'Incident manager', groups: ['incident-managers', 'head-of-operations'] },
  { id: 'david', name: 'David Mensah', role: 'Rule owner and team manager', groups: ['rule-owners', 'team-managers'] },
  { id: 'all', name: 'Operations lead', role: 'All queues', groups: null },
];
const TASK_LABEL = {
  ReviewRecommendation: 'Review AI recommendation', AskSpecialist: 'Question from the investigator', ComplianceBreachReview: 'Statutory deadline missed',
  AskOnCall: 'Question from the triage agent', Acknowledge: 'Acknowledge incident', Resolve: 'Resolve incident',
  RuleOwnerReview: 'Approve rule change', ApproveReward: 'Approve recognition reward',
};
const TYPE_LABEL = { LATE_PAYMENT: 'Late payment', NOT_MINE: 'Not my account', BALANCE_INCORRECT: 'Balance incorrect', NOT_OWED: 'Debt not owed' };
const REC_LABEL = { CORRECT: 'Correct the item', DELETE: 'Delete the item', VERIFY_AS_REPORTED: 'Verify as reported', NEEDS_HUMAN: 'Needs a person' };
const FLAG = { US: 'United States', ZA: 'South Africa', IN: 'India', UK: 'United Kingdom' };
const TOOL_LABEL = {
  GetTradeline: 'Credit file tradeline', GetFurnisherResponse: 'Furnisher response', GetConsumerEvidence: 'Consumer evidence', CheckDisputeHistory: 'Dispute history',
  CheckFraudSignals: 'Identity and fraud signals', LookUpDisputePolicy: 'Dispute policy', ScoreEvidence: 'Evidence strength (decision table)', AskSpecialist: 'Ask a dispute specialist',
  GetServiceHealth: 'Service health', GetRecentChanges: 'Recent changes', SearchSimilarIncidents: 'Similar incidents', GetRunbook: 'Runbook', AskOnCall: 'Ask the on-call engineer',
  ReadChangePolicy: 'Change policy', GetOverrideStats: 'Override statistics', BacktestThreshold: 'Backtest threshold',
};
const HIDE_STEPS = new Set(['SEQUENCE_FLOW', 'EXCLUSIVE_GATEWAY', 'PARALLEL_GATEWAY', 'EVENT_BASED_GATEWAY', 'INCLUSIVE_GATEWAY']);

let CFG = {}; let timer = null; let persona = PERSONAS[0];
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const api = async (p, body) => {
  const r = await fetch(p, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  return r.json();
};
const toast = t => { const el = $('#toast'); el.textContent = t; el.classList.add('on'); setTimeout(() => el.classList.remove('on'), 2600); };
const tm = s => s ? new Date(s).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
const ago = s => { if (!s) return ''; const d = (Date.now() - new Date(s)) / 1000; return d < 60 ? `${Math.round(d)}s ago` : d < 3600 ? `${Math.round(d / 60)} min ago` : `${Math.round(d / 3600)} h ago`; };
const secs = iso => { const m = /PT(\d+)S/.exec(iso || ''); return m ? +m[1] : null; };
const fmtDate = s => s ? new Date(s + 'T12:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
const mine = t => !persona.groups || (t.candidateGroups || []).some(g => persona.groups.includes(g)) || t.assignee === persona.name;
const pj = x => { if (x == null) return ''; if (typeof x === 'string') return x; return JSON.stringify(x, null, 1); };

function setPersona(id) {
  persona = PERSONAS.find(p => p.id === id) || PERSONAS[0];
  try { localStorage.setItem('cb-persona', persona.id); } catch (e) { }
  route();
}

function loop(fn, ms = 2500) {   // a refresh that lands after the view changed has nothing to draw into: ignore it
  const run = () => Promise.resolve().then(fn).catch(() => { });
  clearInterval(timer); run(); timer = setInterval(run, ms);
}

async function route() {
  let h = location.hash.replace(/^#\/?/, '') || 'queue';
  if (h.includes('~')) { const [hh, pid] = h.split('~'); h = hh || 'queue'; const pp = PERSONAS.find(x => x.id === pid); if (pp && pp !== persona) { persona = pp; try { localStorage.setItem('cb-persona', pp.id); } catch (e) { } const sel = $('#persona'); if (sel) sel.value = pp.id; } }
  const [r, arg] = h.split('/');
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.dataset.r === r || (r === 'case' && a.dataset.r === (arg && arg.startsWith('i') ? 'incidents' : 'disputes'))));
  const views = { queue: vQueue, disputes: vDisputes, incidents: vIncidents, improve: vImprove, recognition: vRecognition, governance: vGovernance, case: vCase };
  (views[r] || vQueue)(arg);
}

// ------------------------------------------------------------------ work queue
function vQueue() {
  $('#main').innerHTML = `<h1>My work</h1><div class="sub">${esc(persona.name)}, ${esc(persona.role)}</div><div class="card" id="q"><div class="empty"><span class="spin"></span></div></div>`;
  loop(async () => {
    const tasks = (await api('/api/tasks')).filter(mine).sort((a, b) => (b.priority || 0) - (a.priority || 0));
    const navc = $('#nav a[data-r=queue]'); navc.innerHTML = `My work${tasks.length ? `<span class="count">${tasks.length}</span>` : ''}`;
    $('#q').innerHTML = !tasks.length ? '<div class="empty">No tasks in your queues.</div>' : `<table><thead><tr><th>Priority</th><th>Task</th><th>Case</th><th>Queue</th><th>Due</th><th>Assignee</th><th></th></tr></thead><tbody>
      ${tasks.map(t => {
        const due = t.dueDate ? (new Date(t.dueDate) - Date.now()) / 1000 : null;
        const dueTxt = due == null ? '' : due < 0 ? `<span class="pill bad">overdue ${Math.round(-due)}s</span>` : `<span class="pill ${due < 20 ? 'warn' : ''}">${Math.round(due)}s</span>`;
        return `<tr class="click" onclick="location.hash='#/case/${t.processInstanceKey}'">
        <td><span class="pill ${t.priority >= 90 ? 'bad' : t.priority >= 60 ? 'warn' : ''}">${t.priority ?? ''}</span></td>
        <td><b>${esc(TASK_LABEL[t.elementId] || t.name || t.elementId)}</b><div class="small muted">${esc(t.processName || t.processDefinitionId)}</div></td>
        <td class="mono">${esc(t.businessId || t.processInstanceKey)}</td>
        <td class="small">${esc((t.candidateGroups || []).join(', '))}</td><td>${dueTxt}</td>
        <td class="small">${esc(t.assignee || '')}</td>
        <td>${t.assignee ? '' : `<button class="btn sm ghost" onclick="event.stopPropagation();claim('${t.userTaskKey}')">Claim</button>`}</td></tr>`;
      }).join('')}</tbody></table>`;
  }, 2500);
}
async function claim(k) { await api(`/api/tasks/${k}/claim`, { assignee: persona.name }); toast('Claimed'); route(); }

// ------------------------------------------------------------------ disputes
function vDisputes() {
  const sc = CFG.scenarios || {};
  $('#main').innerHTML = `<h1>Consumer disputes</h1><div class="sub">Statutory reinvestigations in flight and closed</div>
    <div class="bar"><select class="in" id="scn" style="width:auto">${Object.entries(sc).map(([k, v]) => `<option value="${k}">${esc(v.consumerName)} · ${esc(TYPE_LABEL[v.disputeType] || v.disputeType)} · ${v.jurisdiction}</option>`).join('')}</select>
    <button class="btn" onclick="newDispute()">Log dispute</button></div><div class="card" id="dl"><div class="empty"><span class="spin"></span></div></div>`;
  loop(async () => {
    const rows = await api('/api/disputes');
    $('#dl').innerHTML = `<table><thead><tr><th>Dispute</th><th>Consumer</th><th>Type</th><th>Jurisdiction</th><th>Statutory due</th><th>Route</th><th>Outcome</th><th>Status</th></tr></thead><tbody>
    ${rows.map(c => {
      const v = c.vars, a = v.authority || {}, d = v.decision || {}, ck = v.clock || {};
      const st = c.incident ? '<span class="pill bad">Incident</span>' : c.state === 'COMPLETED' ? '<span class="pill ok">Closed</span>' : c.state === 'TERMINATED' ? '<span class="pill">Cancelled</span>' : '<span class="pill">In progress</span>';
      const sla = v.slaStatus === 'AT_RISK' ? ' <span class="pill warn">At risk</span>' : v.slaStatus === 'BREACHED' ? ' <span class="pill bad">Breached</span>' : '';
      const flag = v.serviceIncident ? ' <span class="pill y">Service incident</span>' : '';
      return `<tr class="click" onclick="location.hash='#/case/${c.key}'"><td class="mono">${esc(v.disputeId)}</td><td>${esc(v.consumerName)}</td><td>${esc(TYPE_LABEL[v.disputeType] || v.disputeType)}</td>
      <td>${esc(FLAG[v.jurisdiction] || v.jurisdiction)}</td><td>${ck.dueDate ? fmtDate(ck.dueDate) : ''}${sla}${flag}</td>
      <td>${a.ruleId ? `<span class="pill dark">${esc(a.ruleId)}</span> ${a.route === 'AUTO_APPLY' ? 'Within authority' : esc(a.reviewGroup)}` : d.mode === 'UNVERIFIED' ? '<span class="pill">Not verified</span>' : ''}</td>
      <td>${d.outcome ? esc(REC_LABEL[d.outcome] || d.outcome) + (d.mode === 'OVERRIDDEN' ? ' <span class="pill warn">Override</span>' : '') : ''}</td><td>${st}</td></tr>`;
    }).join('')}</tbody></table>`;
  }, 3000);
}
async function newDispute() { const r = await api('/api/disputes/start', { scenario: $('#scn').value }); toast(`Dispute ${r.disputeId} logged`); if (r.processInstanceKey) setTimeout(() => location.hash = `#/case/${r.processInstanceKey}`, 1500); }

// ------------------------------------------------------------------ incidents
function vIncidents() {
  $('#main').innerHTML = `<h1>Production incidents</h1><div class="sub">Alerts arrive from monitoring by webhook and are triaged before a person is paged</div>
    <div class="bar"><select class="in" id="isc" style="width:auto">${Object.entries(CFG.incidentScenarios || {}).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select>
    <button class="btn" onclick="newAlert()">Send monitoring alert</button></div><div class="card" id="il"><div class="empty"><span class="spin"></span></div></div>`;
  loop(async () => {
    const rows = await api('/api/incidents');
    $('#il').innerHTML = `<table><thead><tr><th>Incident</th><th>Service</th><th>Alert</th><th>Priority</th><th>Escalation</th><th>Raised</th><th>Status</th></tr></thead><tbody>
    ${rows.map(c => {
      const v = c.vars, s = v.severity || {};
      const st = c.state === 'COMPLETED' ? '<span class="pill ok">Resolved</span>' : c.state === 'TERMINATED' ? '<span class="pill">Cancelled</span>' : '<span class="pill">Open</span>';
      return `<tr class="click" onclick="location.hash='#/case/${c.key}'"><td class="mono">${esc(v.incidentId)}</td><td>${esc(v.service)}</td><td>${esc((v.alert || {}).title)}</td>
      <td>${s.priority ? `<span class="pill ${s.priority === 'P1' ? 'bad' : s.priority === 'P2' ? 'warn' : ''}">${s.priority}</span>${s.majorIncident ? ' <span class="pill y">Major</span>' : ''}` : '<span class="spin"></span>'}</td>
      <td>${v.escalationLevel ? `Level ${v.escalationLevel}` : ''}</td><td class="small">${ago(c.start)}</td><td>${st}</td></tr>`;
    }).join('')}</tbody></table>`;
  }, 3000);
}
async function newAlert() { const r = await api('/api/incidents/alert', { scenario: $('#isc').value }); toast(`${r.incidentId} raised via ${r.via}`); }

// ------------------------------------------------------------------ case view (dispute, incident, improvement)
function vCase(key) {
  $('#main').innerHTML = `<div id="cv"><div class="empty"><span class="spin"></span> Loading case</div></div>`;
  let first = true;
  loop(async () => {
    const d = await api(`/api/case/${key}`);
    if (!d.instance) return;
    CASEV = d.variables || {}; CASEAGENT = d.agent;
    if (document.getElementById('docview')) return;
    const pid = d.instance.processDefinitionId;
    const navr = { CB_CreditDispute: 'disputes', CB_IncidentManagement: 'incidents', CB_RuleImprovement: 'improve' }[pid];
    document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.dataset.r === navr));
    const html = pid === 'CB_CreditDispute' ? disputeView(d) : pid === 'CB_IncidentManagement' ? incidentView(d) : pid === 'CB_RuleImprovement' ? improveView(d) : genericView(d);
    if (document.querySelector('#cv details[open]')) return;
    const keep = document.activeElement && document.activeElement.closest && document.activeElement.closest('.right');
    if (first || !keep) { $('#cv').innerHTML = html; first = false; }
    else { const right = $('#cv .right'); const tmp = document.createElement('div'); tmp.innerHTML = html; tmp.querySelector('.right')?.replaceWith(right); $('#cv').innerHTML = ''; $('#cv').append(...tmp.childNodes); }
  }, 2500);
}

function stepsHtml(d, hideIds = []) {
  const seen = new Set();
  const els = d.elements.filter(e => !HIDE_STEPS.has(e.type) && e.name && e.name !== e.id && !e.id.includes('#') && !hideIds.includes(e.id) && !(TOOL_LABEL[e.id]));
  return `<ul class="steps">${els.map(e => {
    const k = e.id + e.start; if (seen.has(k)) return ''; seen.add(k);
    const cls = e.incident ? 'incident' : e.state === 'ACTIVE' ? 'active' : e.state === 'TERMINATED' ? 'term' : '';
    return `<li class="${cls}">${esc(e.name)}<div class="t">${tm(e.start)}${e.state === 'ACTIVE' ? ' · in progress' : e.state === 'TERMINATED' ? ' · interrupted' : ''}</div></li>`;
  }).join('')}</ul>`;
}

// ------------------------------------------------------------------ readable tool results and evidence documents
const NICE = { sloP95Ms: 'SLO p95 (ms)', p95Ms: 'p95 (ms)', errorRatePct: 'Error rate (%)', consumersAffectedEstimate: 'Consumers affected (estimate)', openDisputesInFlight: 'Open disputes in flight', accountRef: 'Account', consumerId: 'Consumer ID', deployedAt: 'Deployed at', serviceId: 'Service', disputes12m: 'Disputes (12 months)' };
const LBL = k => NICE[k] || String(k).replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, c => c.toUpperCase()).replace(/\bId\b/, 'ID').replace(/12m\b/, ' (12 months)');
const val = x => x == null || x === '' ? '<span class="muted">none</span>' : typeof x === 'boolean' ? (x ? 'Yes' : 'No') : typeof x === 'object' ? esc(JSON.stringify(x)) : esc(x);
function kvHtml(o) { return `<div class="kv rk">${Object.entries(o || {}).map(([k, x]) => `<div>${esc(LBL(k))}</div><div>${val(x)}</div>`).join('')}</div>`; }
function tableHtml(rows) {
  if (!rows.length) return '<div class="small muted">None</div>';
  const cols = [...new Set(rows.flatMap(r => Object.keys(r || {})))].slice(0, 6);
  return `<table class="rt"><thead><tr>${cols.map(c => `<th>${esc(LBL(c))}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${val(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function generic(r) {
  if (r == null) return '';
  if (typeof r !== 'object') return `<div class="small">${esc(r)}</div>`;
  if (Array.isArray(r)) return r.every(x => x && typeof x === 'object' && !Array.isArray(x)) ? tableHtml(r) : `<div class="small">${esc(r.join(', '))}</div>`;
  const flat = {}, nested = [];
  Object.entries(r).forEach(([k, x]) => (x && typeof x === 'object') ? nested.push([k, x]) : flat[k] = x);
  return (Object.keys(flat).length ? kvHtml(flat) : '') + nested.map(([k, x]) => `<div class="small muted" style="margin-top:6px">${esc(LBL(k))}</div>${generic(x)}`).join('');
}
function docsFor(v, type) { return (v.evidenceDocuments || []).filter(d => !type || ((d.metadata || {}).customProperties || {}).evidenceType === type); }
function docLink(d, label) { return `<a class="doclink" onclick="openDoc('${esc(d.documentId)}')">${esc(label || (d.metadata || {}).fileName || 'Open document')}</a>`; }
function resultHtml(name, r, v) {
  if (r == null) return '';
  try {
    if (name === 'GetConsumerEvidence') return `<div class="small"><span class="pill ${r.documentStatus === 'DOCUMENTED' ? 'ok' : r.documentStatus === 'PARTIAL' ? 'warn' : ''}">${esc(r.documentStatus)}</span></div>
      ${(r.documents || []).map(d => `<div class="ev"><b>${esc(d.type)}</b>${docsFor(v, d.type).map(x => ' · ' + docLink(x, 'View document')).join('')}<div class="small">${esc(d.finding)}</div></div>`).join('') || `<div class="small muted">${esc(r.consumerStatement || 'No documents supplied')}</div>`}`;
    if (name === 'GetFurnisherResponse') return `<div class="small"><span class="pill ${r.position === 'AGREES_WITH_CONSUMER' ? 'ok' : ''}">${esc(r.code)}</span> ${esc(r.detail)}</div><div class="small muted">${esc(r.acdvId || '')} · ${esc(r.furnisher || '')} · ${esc(tm(r.respondedAt))}</div>`;
    if (name === 'CheckFraudSignals') return `<div class="small">${r.identityVerified ? '<span class="pill ok">Identity verified</span> ' : ''}${(r.signals || []).length ? (r.signals || []).map(x => `<span class="pill bad">${esc(typeof x === 'object' ? (x.type || x.signal || JSON.stringify(x)) : x)}</span>`).join(' ') : 'No fraud signals'}</div>`;
    if (name === 'CheckDisputeHistory') return `<div class="small">${esc(r.disputes12m)} disputes in the last 12 months</div>${(r.items || []).length ? tableHtml(r.items) : ''}`;
    if (name === 'LookUpDisputePolicy') return `<div class="small"><b>${esc(r.regulation)}</b></div><div class="small policy">${esc(r.text)}</div>`;
    if (name === 'ScoreEvidence') return `<div class="small">Score <b>${esc(r.evidenceScore)}</b> <span class="pill">${esc(r.band)}</span></div>`;
    if (name === 'AskSpecialist' || name === 'AskOnCall') return `<div class="small">${esc(typeof r === 'object' ? (r.answer || r.specialistAnswer || r.onCallAnswer || JSON.stringify(r)) : r)}</div>`;
    return generic(r);
  } catch (e) { return `<pre>${esc(pj(r)).slice(0, 900)}</pre>`; }
}
function docsCard(v) {
  const docs = v.evidenceDocuments || [];
  if (!docs.length) return '';
  return `<div class="card"><h3>Evidence documents</h3><div class="docs">${docs.map(d => {
    const m = d.metadata || {}, cp = m.customProperties || {};
    return `<div class="doc" onclick="openDoc('${esc(d.documentId)}')"><img src="/evidence/${esc(cp.thumb || '')}" alt="" loading="lazy"><div><b>${esc(cp.evidenceType || m.fileName)}</b><div class="small muted">${esc(m.fileName)} · ${Math.round((m.size || 0) / 1024)} KB</div></div></div>`;
  }).join('')}</div></div>`;
}
let CASEV = {}, CASEAGENT = null;
function openDoc(id) {
  const d = (CASEV.evidenceDocuments || []).find(x => x.documentId === id); if (!d) return;
  const m = d.metadata || {}, cp = m.customProperties || {};
  const ev = ((CASEAGENT && CASEAGENT.steps) || []).find(s => s.type === 'result' && s.name === 'GetConsumerEvidence');
  const found = ev && ev.result && (ev.result.documents || []).find(x => x.type === cp.evidenceType);
  const url = `/api/documents/${encodeURIComponent(d.documentId)}?contentHash=${encodeURIComponent(d.contentHash || '')}`;
  const el = document.createElement('div'); el.id = 'docview';
  el.innerHTML = `<div class="dv"><div class="dvh"><div><b>${esc(cp.evidenceType || m.fileName)}</b> <span class="small muted">${esc(m.fileName)}</span></div><div class="bar" style="margin:0"><a class="btn ghost sm" href="${url}" target="_blank">Open</a><button class="btn sm" onclick="document.getElementById('docview').remove()">Close</button></div></div>
    <div class="dvb"><object data="${url}#toolbar=0&view=FitH" type="application/pdf" aria-label="Document"><div class="dvimg"><img src="/evidence/${esc(cp.thumb || '')}" alt="${esc(m.fileName)}"></div></object><aside><h3>Document</h3><div class="kv"><div>Case</div><div>${esc(cp.disputeId || CASEV.disputeId)}</div><div>Store</div><div>Camunda (${esc(d.storeId || '')})</div><div>Type</div><div>${esc(m.contentType)}</div><div>Size</div><div>${Math.round((m.size || 0) / 1024)} KB</div></div>
    ${found ? `<h3 style="margin-top:16px">Read by the investigator</h3><div class="small">${esc(found.finding)}</div>` : ''}</aside></div></div>`;
  el.onclick = e => { if (e.target === el) el.remove(); };
  document.body.appendChild(el);
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('docview')?.remove(); });

function trailHtml(a, v, modelKey) {
  if (!a) return '<div class="empty"><span class="spin"></span> Waiting to start</div>';
  const m = a.metrics || {}, usage = m.tokenUsage || {};
  const results = Object.fromEntries(a.steps.filter(s => s.type === 'result').map(s => [s.id, s]));
  const rows = a.steps.filter(s => s.type !== 'result').map(s => {
    if (s.type === 'answer') return `<div class="row"><div class="ico a">✓</div><div><div class="nm">Recommendation returned</div><div class="t small muted">${tm(s.at)}</div></div></div>`;
    const r = results[s.id];
    const args = s.args && Object.keys(s.args).length ? ` <span class="muted small">${esc(Object.entries(s.args).map(([k, x]) => `${k}: ${typeof x === 'object' ? JSON.stringify(x) : x}`).join(', '))}</span>` : '';
    return `<div class="row"><div class="ico ${r ? 'r' : ''}">${r ? '✓' : '…'}</div><div><div class="nm">${esc(TOOL_LABEL[s.name] || s.name)}${args}</div>${r ? `<div class="res">${resultHtml(s.name, r.result, v)}</div><details class="raw"><summary>JSON</summary><pre>${esc(pj(r.result)).slice(0, 2000)}</pre></details>` : '<span class="spin"></span>'}</div></div>`;
  });
  const prompt = v.obsPromptVersion ? `v${esc(v.obsPromptVersion)}` : '';
  return `<div class="metrics"><span>Model <b>${esc(v.obsModel || (CFG.genai || {}).model || 'gateway model')}</b></span><span>Model calls <b>${m.modelCalls ?? 0}</b></span><span>Tool calls <b>${m.toolCalls ?? 0}</b></span>
    <span>Tokens <b>${((usage.inputTokenCount || 0) + (usage.outputTokenCount || 0)).toLocaleString()}</b></span>${prompt ? `<span>Prompt <b>${prompt}</b></span>` : ''}</div>
    <div class="trail">${rows.join('') || '<div class="empty"><span class="spin"></span> Thinking</div>'}</div>`;
}

function decisionsHtml(d) {
  if (!d.decisions.length) return '<div class="empty">No decisions yet</div>';
  return d.decisions.map(x => {
    const rule = (x.matchedRules || []).map(r => r.ruleId).join(', ');
    const out = x.matchedRules && x.matchedRules[0] ? x.matchedRules[0].outputs : (x.result || {});
    return `<div class="dmn"><div class="hd"><span class="nm">${esc(x.name || x.decisionId)}</span><span><span class="pill dark">${esc(rule || 'no rule')}</span> <span class="pill">v${x.version}</span></span></div>
    <div class="io"><div><div class="small muted">Inputs</div>${Object.entries(x.inputs).map(([k, v]) => `${esc(k)}: <b>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</b>`).join('<br>')}</div>
    <div><div class="small muted">Result</div>${Object.entries(out || {}).map(([k, v]) => `${esc(k)}: <b>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</b>`).join('<br>')}</div></div></div>`;
  }).join('');
}

function clockHtml(d, v) {
  const ck = v.clock; if (!ck) return '';
  const total = secs(ck.breachAfter), warn = secs(ck.warnAfter);
  const startEl = d.elements.find(e => e.id === 'Reinvestigation');
  if (!total || !startEl) return '';
  const end = d.instance.state !== 'ACTIVE' ? new Date(d.instance.endDate) : (startEl.end ? new Date(startEl.end) : new Date());
  const el = Math.max(0, (end - new Date(startEl.start)) / 1000);
  const pct = Math.min(100, 100 * el / total);
  const daysUsed = Math.min(ck.slaDays, Math.round(ck.slaDays * el / total));
  return `<div class="clock"><div class="track"><div class="fill ${el >= total ? 'bad' : el >= warn ? 'warn' : ''}" style="width:${pct}%"></div></div>
    <div class="lbl"><span>Day ${daysUsed} of ${ck.slaDays}${ck.businessDays ? ' business days' : ''}</span><span>Due ${fmtDate(ck.dueDate)}</span></div></div>`;
}

function myTask(d, ids) { return d.tasks.find(t => t.state === 'CREATED' && ids.includes(t.elementId)); }

function taskPanel(d, t) {
  if (!t) return '';
  const can = mine(t);
  const v = d.variables;
  const head = `<div class="hd"><h2>${esc(TASK_LABEL[t.elementId] || t.name)}</h2><div class="small muted">Queue: ${esc((t.candidateGroups || []).join(', '))} · priority ${t.priority ?? ''}${t.assignee ? ' · ' + esc(t.assignee) : ''}</div></div>`;
  if (!can) return `<div class="card">${head}<div class="banner info">This task is in another team's queue. Switch persona to act on it.</div></div>`;
  const k = t.userTaskKey;
  if (t.elementId === 'ReviewRecommendation') {
    const a = v.assessment || {};
    return `<div class="card">${head}
      <label class="f">Your decision</label>
      <select class="in" id="dec" onchange="document.getElementById('ov').style.display=this.value==='OVERRIDE'?'block':'none'"><option value="APPROVE">Approve: ${esc(REC_LABEL[a.recommendation] || a.recommendation)}</option><option value="OVERRIDE">Override</option></select>
      <div id="ov" style="display:none"><label class="f">Outcome instead</label><select class="in" id="ovo"><option value="CORRECT">Correct the item</option><option value="DELETE">Delete the item</option><option value="VERIFY_AS_REPORTED">Verify as reported</option></select>
      <label class="f">Reason</label><textarea class="in" id="ovr" style="min-height:70px"></textarea></div>
      <label class="f">Letter to ${esc(v.consumerName)}</label><textarea class="in" id="letter">${esc(a.consumerLetter || '')}</textarea>
      <div class="bar" style="margin-top:12px"><button class="btn" onclick="submitReview('${k}')">Submit decision</button></div></div>`;
  }
  const simple = {
    AskSpecialist: ['specialistAnswer', 'Your answer', v.agentQuestion], AskOnCall: ['onCallAnswer', 'Your answer', v.agentQuestion],
    Acknowledge: ['ackNote', 'Acknowledgement note', ''],
  }[t.elementId];
  if (simple) return `<div class="card">${head}${simple[2] ? `<div class="letter">${esc(simple[2])}</div>` : ''}<label class="f">${simple[1]}</label><textarea class="in" id="ans" style="min-height:80px"></textarea>
    <div class="bar" style="margin-top:12px"><button class="btn" onclick="submitSimple('${k}','${simple[0]}')">${t.elementId === 'Acknowledge' ? 'Acknowledge and take ownership' : 'Send answer'}</button></div></div>`;
  if (t.elementId === 'Resolve') return `<div class="card">${head}<label class="f">What fixed it</label><textarea class="in" id="res" style="min-height:70px">Rolled back ${esc((v.triage || {}).linkedChange || 'the change')}; latency back under SLO.</textarea>
    <label class="f">Root cause</label><input class="in" id="rc" value="${esc((v.triage || {}).suspectedCause || '')}"><label class="f">Was the AI triage right?</label>
    <select class="in" id="acc"><option value="YES">Yes</option><option value="PARTLY">Partly</option><option value="NO">No</option></select>
    <div class="bar" style="margin-top:12px"><button class="btn" onclick="submitResolve('${k}')">Resolve incident</button></div></div>`;
  if (t.elementId === 'RuleOwnerReview') return `<div class="card">${head}<label class="f">Comments</label><textarea class="in" id="rcm" style="min-height:60px"></textarea>
    <div class="bar" style="margin-top:12px"><button class="btn" onclick="submitRule('${k}','APPROVE')">Approve and deploy</button><button class="btn ghost" onclick="submitRule('${k}','REJECT')">Reject</button></div></div>`;
  if (t.elementId === 'ComplianceBreachReview') return `<div class="card">${head}<select class="in" id="ca"><option value="DELETE_NOW">Delete the disputed item now</option><option value="LOG_EXCEPTION">Record exception in the regulator log</option></select>
    <div class="bar" style="margin-top:12px"><button class="btn" onclick="complete('${k}',{complianceAction:document.getElementById('ca').value})">Record action</button></div></div>`;
  return `<div class="card">${head}<button class="btn" onclick="complete('${k}',{})">Complete</button></div>`;
}

async function complete(k, vars) { await api(`/api/tasks/${k}/complete`, { variables: vars, assignee: persona.name }); toast('Submitted'); }
function submitReview(k) {
  const dec = $('#dec').value;
  const vars = { analystDecision: dec, consumerLetter: $('#letter').value, reviewerName: persona.name };
  if (dec === 'OVERRIDE') { vars.overrideOutcome = $('#ovo').value; vars.overrideReason = $('#ovr').value || 'Analyst judgement'; }
  complete(k, vars);
}
function submitSimple(k, field) { complete(k, { [field]: $('#ans').value || 'Acknowledged', resolverName: persona.name }); }
function submitResolve(k) { complete(k, { resolution: $('#res').value, rootCause: $('#rc').value, triageAccurate: $('#acc').value, resolverName: persona.name }); }
function submitRule(k, d) { complete(k, { ruleOwnerDecision: d, ruleOwnerComments: $('#rcm').value }); }
async function unlock(did, pik) { await api(`/api/ops/unlock/${did}`, {}); toast('Lock released'); }
async function retry(pik) { await api(`/api/ops/retry/${pik}`, {}); toast('Retried'); }

function disputeView(d) {
  const v = d.variables, a = v.assessment, au = v.authority || {}, dec = v.decision, inst = d.instance;
  const open = d.tasks.filter(t => t.state === 'CREATED');
  const banners = [];
  if (d.incidents.length) banners.push(`<div class="banner bad"><div><b>Credit file update failed.</b> ${esc(d.incidents[0].errorMessage)}</div><div>${d.locked ? `<button class="btn sm warn" onclick="unlock('${esc(v.disputeId)}')">Release lock</button>` : ''} <button class="btn sm" onclick="retry('${inst.processInstanceKey}')">Retry</button></div></div>`);
  if (v.slaStatus === 'AT_RISK') banners.push(`<div class="banner warn"><div><b>75% of the statutory clock used.</b> Team lead alerted; open tasks raised to priority 95.</div></div>`);
  if (v.slaStatus === 'BREACHED') banners.push(`<div class="banner bad"><div><b>Statutory deadline reached.</b> Compliance review opened.</div></div>`);
  if (v.serviceIncident) banners.push(`<div class="banner info"><div><b>Service incident ${esc(v.serviceIncident.incidentId)} (${esc(v.serviceIncident.priority)})</b> affects dispute intake. Statutory clock flagged at risk.</div></div>`);
  if (v.reviewEscalated) banners.push(`<div class="banner warn"><div><b>Review SLA missed.</b> Reassigned to ${esc(v.escalatedTo || 'senior analysts')} with priority 95.</div></div>`);
  const fr = v.furnisherResponse;
  const status = inst.state === 'COMPLETED' ? '<span class="pill ok">Closed</span>' : inst.state === 'TERMINATED' ? '<span class="pill">Cancelled</span>' : '<span class="pill">In progress</span>';
  const recCard = a ? `<div class="card rec"><h3>AI recommendation</h3>
     <div class="bar" style="justify-content:space-between"><span class="big">${esc(REC_LABEL[a.recommendation] || a.recommendation)}</span><span class="score">Evidence score <b>${esc(a.confidence)}</b></span></div>
     ${a.correction && a.correction !== 'none' ? `<div class="small"><b>Change:</b> ${esc(a.correction)}</div>` : ''}
     <p>${esc(a.summary)}</p>
     <ul class="evid">${(a.evidence || []).map(e => `<li><b>${esc(TOOL_LABEL[e.source] || e.source)}:</b> ${esc(e.finding)}${e.source === 'GetConsumerEvidence' ? docsFor(v).map(x => ' ' + docLink(x, '[' + (((x.metadata || {}).customProperties || {}).evidenceType || 'document') + ']')).join('') : ''}</li>`).join('')}</ul>
     ${a.policyClause ? `<div class="small muted" style="margin-top:8px">Policy relied on: ${esc(a.policyClause)}</div>` : ''}
     ${au.ruleId ? `<div class="banner info" style="margin:12px 0 0"><div><span class="pill dark">${esc(au.ruleId)}</span> ${esc(au.reason)}</div></div>` : ''}</div>` : '';
  const outcome = dec ? `<div class="card"><h3>Outcome</h3><div class="bar"><span class="pill ${dec.mode === 'OVERRIDDEN' ? 'warn' : 'ok'}">${esc(dec.mode)}</span><b>${esc(REC_LABEL[dec.outcome] || dec.outcome)}</b><span class="muted">by ${esc(dec.decidedBy)}</span></div>
     ${dec.reason ? `<div class="small"><b>Override reason:</b> ${esc(dec.reason)}</div>` : ''}${dec.letter ? `<label class="f">Letter sent to ${esc(v.consumerName)}</label><div class="letter">${esc(dec.letter)}</div>` : ''}</div>` : '';
  return `<div class="bar" style="justify-content:space-between"><div><h1>${esc(v.disputeId)} · ${esc(v.consumerName)}</h1><div class="sub" style="margin:0">${esc(TYPE_LABEL[v.disputeType] || v.disputeType)} · ${esc(v.furnisher)} ${esc(v.accountRef)} · ${esc(v.channel)}</div></div>
    <div class="bar">${status}<a class="btn ghost sm" href="${d.operate}" target="_blank">Open in Operate</a></div></div>
    ${banners.join('')}
    <div class="case">
      <div class="left"><div class="card"><h3>Case</h3><div class="kv"><div>Jurisdiction</div><div>${esc(FLAG[v.jurisdiction] || v.jurisdiction)}</div><div>Regulation</div><div>${esc((v.clock || {}).regulation || '')}</div>
        <div>Clock</div><div>${v.clock ? `${v.clock.slaDays} ${v.clock.businessDays ? 'business' : 'calendar'} days` : ''}</div><div>Public holidays</div><div>${v.clock ? v.clock.holidaysConsidered + ' loaded' : ''}</div>
        <div>Vulnerable</div><div>${v.vulnerableConsumer ? '<span class="pill warn">Yes</span>' : 'No'}</div><div>Identity</div><div>${v.identity ? '<span class="pill ok">Verified</span>' : ''}</div></div>${clockHtml(d, v)}
        <label class="f">Consumer statement</label><div class="small">${esc(v.consumerStatement)}</div>
        ${fr ? `<label class="f">Furnisher response (${esc(fr.acdvId || '')})</label><div class="small"><span class="pill ${fr.position === 'AGREES_WITH_CONSUMER' ? 'ok' : ''}">${esc(fr.code)}</span> ${esc(fr.detail)}</div>` : v.acdv ? `<label class="f">Furnisher</label><div class="small"><span class="spin"></span> Awaiting ${esc(v.acdv.acdvId)} via ${esc(v.acdv.channel)}</div>` : ''}
        </div>${docsCard(v)}<div class="card"><h3>Progress</h3>${stepsHtml(d)}</div></div>
      <div class="mid">${recCard}<div class="card"><h3>Investigation</h3>${trailHtml(d.agent, v)}</div><div class="card"><h3>Decisions</h3>${decisionsHtml(d)}</div></div>
      <div class="right">${open.map(t => taskPanel(d, t)).join('')}${outcome}</div>
    </div>`;
}

function incidentView(d) {
  const v = d.variables, tr = v.triage, s = v.severity || {}, inst = d.instance, al = v.alert || {};
  const open = d.tasks.filter(t => t.state === 'CREATED');
  const status = inst.state === 'COMPLETED' ? '<span class="pill ok">Resolved</span>' : '<span class="pill">Open</span>';
  const banners = [];
  if (v.flaggedDisputes != null) banners.push(`<div class="banner info"><div><b>${v.flaggedDisputes} open disputes flagged.</b> Their statutory clocks are marked at risk.</div></div>`);
  if (v.escalationLevel) banners.push(`<div class="banner warn"><div><b>Not acknowledged in time.</b> Escalated to ${esc(v.escalatedTo)} (level ${v.escalationLevel}).</div></div>`);
  return `<div class="bar" style="justify-content:space-between"><div><h1>${esc(v.incidentId)} · ${esc(al.title)}</h1><div class="sub" style="margin:0">${esc(al.source)} · ${esc((v.serviceInfo || {}).name || v.service)} · tier ${esc((v.serviceInfo || {}).tier)}</div></div>
    <div class="bar">${s.priority ? `<span class="pill ${s.priority === 'P1' ? 'bad' : 'warn'}">${s.priority}</span>` : ''}${s.majorIncident ? '<span class="pill y">Major incident</span>' : ''}${status}<a class="btn ghost sm" href="${d.operate}" target="_blank">Open in Operate</a></div></div>
    ${banners.join('')}
    <div class="case"><div class="left"><div class="card"><h3>Service</h3><div class="kv"><div>Owner</div><div>${esc((v.serviceInfo || {}).owner)}</div><div>Resolver</div><div>${esc((v.serviceInfo || {}).resolverGroup)}</div>
      <div>Customer facing</div><div>${(v.serviceInfo || {}).customerFacing ? 'Yes' : 'No'}</div><div>Regulatory</div><div>${(v.serviceInfo || {}).regulatoryService ? 'Yes' : 'No'}</div>
      <div>Acknowledge in</div><div>${s.ackMinutes ? s.ackMinutes + ' min' : ''}</div><div>Resolve in</div><div>${s.resolveHours ? s.resolveHours + ' h' : ''}</div></div></div>
      <div class="card"><h3>Progress</h3>${stepsHtml(d)}</div></div>
      <div class="mid">${tr ? `<div class="card rec"><h3>AI triage</h3><div class="bar"><span class="pill">Impact ${esc(tr.impact)}</span><span class="pill">Urgency ${esc(tr.urgency)}</span>${tr.linkedChange && tr.linkedChange !== 'none' ? `<span class="pill y">${esc(tr.linkedChange)}</span>` : ''}${tr.similarIncident && tr.similarIncident !== 'none' ? `<span class="pill">Like ${esc(tr.similarIncident)}</span>` : ''}</div>
        <p>${esc(tr.summary)}</p><div class="small"><b>Suspected cause:</b> ${esc(tr.suspectedCause)}</div><ol class="evid">${(tr.firstActions || []).map(x => `<li>${esc(x)}</li>`).join('')}</ol></div>` : ''}
      <div class="card"><h3>Investigation</h3>${trailHtml(d.agent, v)}</div><div class="card"><h3>Decisions</h3>${decisionsHtml(d)}</div></div>
      <div class="right">${open.map(t => taskPanel(d, t)).join('')}${v.resolutionRecord ? `<div class="card"><h3>Resolution</h3><div class="small">${esc(v.resolutionRecord.resolution)}</div><div class="small muted">Triage accurate: ${esc(v.resolutionRecord.triageAccurate)}</div></div>` : ''}</div></div>`;
}

function improveView(d) {
  const v = d.variables, p = v.proposal, val = v.validation;
  const open = d.tasks.filter(t => t.state === 'CREATED');
  return `<div class="bar" style="justify-content:space-between"><div><h1>Rule improvement review</h1><div class="sub" style="margin:0">Dispute approval authority · started ${tm(d.instance.startDate)}</div></div>
    <a class="btn ghost sm" href="${d.operate}" target="_blank">Open in Operate</a></div>
    <div class="case"><div class="left"><div class="card"><h3>Progress</h3>${stepsHtml(d)}</div></div>
    <div class="mid">${p ? `<div class="card rec"><h3>Proposal</h3>${p.change ? `<div class="bar"><span class="big">${esc(p.parameter)}: ${esc(p.currentValue)} → ${esc(p.proposedValue)}</span></div>` : '<b>No change proposed</b>'}
      <p>${esc(p.rationale)}</p>${p.backtest ? `<div class="kv"><div>Cases affected</div><div>${esc(p.backtest.casesAffected)}</div><div>Overridden among them</div><div>${esc(p.backtest.overriddenAmongAffected)}</div></div>` : ''}
      <div class="small muted" style="margin-top:8px">Risk: ${esc(p.risk)}</div>
      ${val ? `<div class="banner ${val.ok ? 'info' : 'bad'}" style="margin:12px 0 0"><div>${val.ok ? 'Within change policy' : 'Outside change policy'}: ${esc(val.checkedAgainst)}</div></div>` : ''}
      ${v.deployedVersion ? `<div class="banner info" style="margin:12px 0 0"><div><b>Deployed CB_DisputeApprovalAuthority version ${v.deployedVersion}.</b> New cases use it; in-flight cases keep the version they started with.</div></div>` : ''}</div>` : ''}
      <div class="card"><h3>Analysis</h3>${trailHtml(d.agent, v)}</div></div>
    <div class="right">${open.map(t => taskPanel(d, t)).join('')}</div></div>`;
}

function genericView(d) { return `<h1>${esc(d.instance.processDefinitionName || d.instance.processDefinitionId)}</h1><div class="card">${stepsHtml(d)}</div><div class="card">${decisionsHtml(d)}</div>`; }

// ------------------------------------------------------------------ improvement
function vImprove() {
  $('#main').innerHTML = `<h1>Rule improvement</h1><div class="sub">How analysts decided on AI recommendations, turned into a governed rule change</div>
  <div class="bar"><button class="btn" onclick="startImprove()">Run improvement review</button></div><div class="card" id="imp"><div class="empty"><span class="spin"></span></div></div>`;
  loop(async () => {
    const rows = await api('/api/improvements');
    $('#imp').innerHTML = !rows.length ? '<div class="empty">No reviews yet.</div>' : `<table><thead><tr><th>Started</th><th>Proposal</th><th>Policy check</th><th>Deployed</th><th>Status</th></tr></thead><tbody>
      ${rows.map(c => { const v = c.vars, p = v.proposal || {}; return `<tr class="click" onclick="location.hash='#/case/${c.key}'"><td>${tm(c.start)}</td><td>${p.change ? `${esc(p.parameter)} ${p.currentValue} → ${p.proposedValue}` : p.rationale ? 'No change' : '<span class="spin"></span>'}</td>
        <td>${v.validation ? (v.validation.ok ? '<span class="pill ok">Pass</span>' : '<span class="pill bad">Fail</span>') : ''}</td><td>${v.deployedVersion ? 'v' + v.deployedVersion : ''}</td><td>${c.state === 'COMPLETED' ? '<span class="pill ok">Done</span>' : '<span class="pill">Open</span>'}</td></tr>`; }).join('')}</tbody></table>`;
  }, 3000);
}
async function startImprove() { const r = await api('/api/improvements/start', { requestedBy: persona.name }); toast('Review started'); if (r.processInstanceKey) setTimeout(() => location.hash = `#/case/${r.processInstanceKey}`, 1500); }

// ------------------------------------------------------------------ recognition
function vRecognition() {
  $('#main').innerHTML = `<h1>Operations Guardians</h1><div class="sub">Points for resolved cases, clocks kept and improvements adopted</div><div class="grid g2"><div class="card" id="lb"></div><div><div class="card" id="rw"></div><div class="card" id="feed"></div></div></div>`;
  loop(async () => {
    const [d, tasks] = await Promise.all([api('/api/leaderboard'), api('/api/tasks')]);
    const max = Math.max(1, ...d.rows.map(r => r.total));
    $('#lb').innerHTML = `<h2>Leaderboard</h2><table class="lb"><tbody>${d.rows.map((r, i) => `<tr><td class="rank">${i + 1}</td><td><b>${esc(r.employee)}</b><div>${r.badges.map(b => `<span class="badge">${esc(b)}</span>`).join('')}</div></td>
      <td style="width:38%"><div class="barw"><div style="width:${100 * r.total / max}%"></div></div></td><td style="text-align:right"><b>${r.total}</b></td></tr>`).join('')}</tbody></table>`;
    const rewards = tasks.filter(t => t.elementId === 'ApproveReward');
    $('#rw').innerHTML = `<h2>Rewards awaiting approval</h2>${rewards.length ? rewards.map(t => `<div class="bar" style="justify-content:space-between"><span>${esc(t.businessId || t.processInstanceKey)}</span>
      ${persona.groups && !persona.groups.includes('team-managers') ? '<span class="small muted">Team managers approve</span>' : `<button class="btn sm" onclick="complete('${t.userTaskKey}',{managerDecision:'APPROVE',managerNote:'Well earned'})">Approve</button>`}</div>`).join('') : '<div class="empty">None</div>'}`;
    $('#feed').innerHTML = `<h2>Recent points</h2>${d.recent.length ? `<table><tbody>${d.recent.map(r => `<tr><td>${esc(r.employee)}</td><td class="small">${esc(r.event_type.replace(/_/g, ' ').toLowerCase())}</td><td class="mono small">${esc(r.source_ref)}</td><td style="text-align:right"><b>+${r.points}</b></td></tr>`).join('')}</tbody></table>` : '<div class="empty">No points yet today</div>'}`;
  }, 3000);
}

// ------------------------------------------------------------------ governance
function vGovernance() {
  $('#main').innerHTML = `<h1>AI governance</h1><div class="sub">Every agent run, the prompt and model it used, and how people decided on it</div><div id="gv"><div class="empty"><span class="spin"></span></div></div>`;
  loop(async () => {
    const g = await api('/api/governance');
    const k = g.kpi || {};
    const agents = (g.agents || []).slice(0, 25);
    const tok = agents.reduce((s, a) => s + (a.tokens || 0), 0);
    $('#gv').innerHTML = `<div class="kpis"><div class="kpi"><div class="v">${k.completed ?? 0}</div><div class="l">Disputes closed</div></div><div class="kpi"><div class="v">${k.auto ?? 0}</div><div class="l">Applied within authority</div></div>
      <div class="kpi"><div class="v">${k.reviewed ?? 0}</div><div class="l">Signed off by a person</div></div><div class="kpi"><div class="v">${k.agreement == null ? '–' : k.agreement + '%'}</div><div class="l">Analyst agreement with AI</div></div>
      <div class="kpi"><div class="v">${k.overridden ?? 0}</div><div class="l">Overrides</div></div><div class="kpi"><div class="v">${(g.policy || {}).autoApplyMinConfidence ?? ''}</div><div class="l">Auto-apply threshold (A-06)</div></div>
      <div class="kpi"><div class="v">${agents.length ? Math.round(tok / agents.length).toLocaleString() : '–'}</div><div class="l">Tokens per agent run (last ${agents.length})</div></div></div>
    <div class="grid g2"><div class="card"><h2>Prompt registry</h2><table><thead><tr><th>Prompt</th><th>Version</th><th>Owner</th><th>Approved</th></tr></thead><tbody>${Object.entries(g.prompts || {}).map(([n, p]) => `<tr><td class="mono">${esc(p.id)}</td><td>v${esc(p.version)}</td><td>${esc(p.owner)}</td><td>${esc(p.approvedOn)}</td></tr>`).join('')}</tbody></table>
      <div class="small muted" style="margin-top:10px">Model gateway: ${esc((g.genai || {}).endpoint)} · ${esc((g.genai || {}).model)}</div></div>
      <div class="card"><h2>Change policy</h2><div class="kv"><div>Tunable</div><div>${esc((g.policy || {}).parameter)}</div><div>Limits</div><div>${esc(JSON.stringify((g.policy || {}).limits || {}))}</div><div>Locked rules</div><div class="small">${esc((g.policy || {}).rules)}</div></div></div></div>
    <div class="card"><h2>Agent runs</h2>${agents.length ? `<table><thead><tr><th>Agent</th><th>Started</th><th>Process instance</th><th>Status</th><th>Model</th><th>Model calls</th><th>Tool calls</th><th>Tokens</th></tr></thead><tbody>${agents.map(a => `<tr class="click" onclick="location.hash='#/case/${a.processInstanceKey}'"><td><b>${esc(a.agent)}</b></td><td class="small">${tm(a.created)}</td><td class="mono">${esc(a.processInstanceKey)}</td><td><span class="pill">${esc(a.status)}</span></td><td class="small">${esc(a.model)}</td>
        <td>${a.modelCalls ?? ''} <span class="muted small">/ ${a.maxModelCalls ?? ''}</span></td><td>${a.toolCalls ?? ''}</td><td>${(a.tokens || 0).toLocaleString()}</td></tr>`).join('')}</tbody></table>` : `<div class="empty">Agent instance API returned ${g.agentStatus}.</div>`}</div>
    <div class="card"><h2>Decisions by people on AI recommendations</h2><table><thead><tr><th>Dispute</th><th>Type</th><th>AI</th><th>Score</th><th>Rule</th><th>Person</th><th>Final</th><th>Reason</th></tr></thead><tbody>${(g.outcomes || []).map(o => `<tr><td class="mono">${esc(o.dispute_id)}</td><td>${esc(TYPE_LABEL[o.dispute_type] || o.dispute_type)}</td><td>${esc(o.recommendation || '')}</td><td>${o.confidence ?? ''}</td><td>${esc(o.rule || o.route)}</td>
      <td>${o.analyst_decision === 'OVERRIDDEN' ? '<span class="pill warn">Override</span>' : o.analyst_decision === 'APPROVED' ? '<span class="pill ok">Approved</span>' : '<span class="pill">Not required</span>'}</td><td>${esc(o.final_outcome)}</td><td class="small">${esc(o.override_reason || '')}</td></tr>`).join('')}</tbody></table></div>`;
  }, 5000);
}

// ------------------------------------------------------------------ boot
(async function boot() {
  CFG = await api('/api/config');
  const sel = $('#persona');
  sel.innerHTML = PERSONAS.map(p => `<option value="${p.id}">${esc(p.name)} · ${esc(p.role)}</option>`).join('');
  let saved = null; try { saved = localStorage.getItem('cb-persona'); } catch (e) { }
  persona = PERSONAS.find(p => p.id === saved) || PERSONAS[0]; sel.value = persona.id;
  sel.onchange = () => setPersona(sel.value);
  $('#navfoot').innerHTML = `<a href="${CFG.tasklist}" target="_blank" style="padding:0;color:#9cc7d1">Tasklist</a> · <a href="${CFG.operate}" target="_blank" style="padding:0;color:#9cc7d1">Operate</a>`;
  window.addEventListener('hashchange', route);
  window.addEventListener('storage', e => { if (e.key === 'cb-persona' && e.newValue && e.newValue !== persona.id) { sel.value = e.newValue; setPersona(e.newValue); } });
  route();
})();
