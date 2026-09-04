(() => {
  'use strict';
  const REPO = { owner: 'alancullinan', name: 'fpl-picker', workflow: 'update-data.yml' };
  const POS = { 1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD' };
  const CHIP_LABEL = { wildcard: 'WC', freehit: 'FH', bboost: 'BB', '3xc': 'TC' };
  const CHIP_NAME = { wildcard: 'Wildcard', freehit: 'Free Hit', bboost: 'Bench Boost', '3xc': 'Triple Captain' };
  const CHIP_ORDER = ['wildcard', 'freehit', 'bboost', '3xc'];
  const STATUS = { a: '', d: 'Doubt', i: 'Injured', s: 'Suspended', u: 'Unavailable', n: 'Not in squad' };
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d = 1) => (v == null ? '-' : Number(v).toFixed(d));
  const money = (v) => '£' + Number(v).toFixed(1) + 'm';
  const commas = (v) => (v == null ? '-' : Number(v).toLocaleString('en-GB'));
  const fit = (p) => p.status === 'a' || (p.chance != null && p.chance >= 75);
  // Expected points over the next n gameweeks (the bundle carries 8 per player).
  const xpN = (p, n) => (p.xp_gw || []).slice(0, n).reduce((a, b) => a + b, 0);
  const xh = () => S.xh;

  let D = null;
  let byId = new Map();
  const defaults = { view: 'team', pos: 0, team: 0, price: '', avail: true, mine: false, search: '', sort: 'xpn', dir: -1, horizon: 5, xh: 5 };
  let S = { ...defaults };
  try { S = { ...defaults, ...JSON.parse(localStorage.getItem('fplpicker') || '{}') }; } catch (e) { /* fresh */ }
  const persist = () => { try { localStorage.setItem('fplpicker', JSON.stringify(S)); } catch (e) { /* ignore */ } };

  // ---------- plan (planning mode state, browser only) ----------
  // { base_gw, picks: [{id, slot, c, vc}], swaps: [{out, in}], chip }
  let P = null;
  let pendingSwap = null; // player id awaiting a second tap to swap slots
  const loadPlan = () => { try { P = JSON.parse(localStorage.getItem('fplplan') || 'null'); } catch (e) { P = null; } };
  const savePlan = () => { try { if (P) localStorage.setItem('fplplan', JSON.stringify(P)); else localStorage.removeItem('fplplan'); } catch (e) { /* ignore */ } };
  function freshPlan() { return { base_gw: D.me.picks_gw, picks: D.me.picks.map((x) => ({ ...x })), swaps: [], chip: null }; }
  function startPlan() { if (!P && D.me && D.me.picks.length) { P = freshPlan(); savePlan(); } }
  function resetPlan() { P = freshPlan(); pendingSwap = null; savePlan(); renderTeam(); renderPlayers(); }
  // Does the plan differ from the confirmed squad?
  function planChanges() {
    if (!P) return null;
    const base = D.me.picks, cur = P.picks;
    const baseXI = new Set(base.filter((x) => x.slot <= 11).map((x) => x.id)), curXI = new Set(cur.filter((x) => x.slot <= 11).map((x) => x.id));
    const swappedIn = new Set(P.swaps.map((x) => x.in)), swappedOut = new Set(P.swaps.map((x) => x.out));
    const starts = [...curXI].filter((id) => !baseXI.has(id) && !swappedIn.has(id));
    const benched = [...baseXI].filter((id) => !curXI.has(id) && !swappedOut.has(id));
    const baseC = (base.find((x) => x.c) || {}).id, curC = (cur.find((x) => x.c) || {}).id;
    const baseV = (base.find((x) => x.vc) || {}).id, curV = (cur.find((x) => x.vc) || {}).id;
    return { swaps: P.swaps, starts, benched, captain: baseC !== curC ? { from: baseC, to: curC } : null, vice: baseV !== curV ? { from: baseV, to: curV } : null, chip: P.chip,
      any: P.swaps.length || starts.length || benched.length || baseC !== curC || baseV !== curV || !!P.chip };
  }
  function picks() { return (P ? P.picks : D.me.picks).map((pk) => ({ ...pk, p: byId.get(pk.id) })).filter((x) => x.p); }
  function squadIds() { return new Set(picks().map((x) => x.id)); }
  function bank() {
    let b = D.me.bank;
    if (P) for (const s of P.swaps) b += byId.get(s.out).price - byId.get(s.in).price;
    return Math.round(b * 10) / 10;
  }
  function hit() {
    if (!P || P.chip === 'wildcard' || P.chip === 'freehit') return 0;
    const ft = D.me.free_transfers ?? 1;
    return Math.max(0, P.swaps.length - ft) * 4;
  }
  function clubCount(excludeId) {
    const c = {};
    for (const x of picks()) { if (x.id === excludeId) continue; c[x.p.team] = (c[x.p.team] || 0) + 1; }
    return c;
  }
  function candidates(outP) {
    const ids = squadIds(), clubs = clubCount(outP.id), budget = bank() + outP.price;
    return D.players.filter((q) => q.pos === outP.pos && !ids.has(q.id) && q.price <= budget + 1e-9 && (clubs[q.team] || 0) < 3)
      .sort((a, c) => xpN(c, xh()) - xpN(a, xh()));
  }
  function applyTransfer(outId, inId) {
    startPlan();
    const pk = P.picks.find((x) => x.id === outId);
    if (!pk) return;
    pk.id = inId;
    const back = P.swaps.findIndex((s) => s.out === inId && s.in === outId);
    if (back >= 0) P.swaps.splice(back, 1);
    else {
      const chain = P.swaps.find((s) => s.in === outId);
      if (chain) { chain.in = inId; if (chain.in === chain.out) P.swaps.splice(P.swaps.indexOf(chain), 1); }
      else P.swaps.push({ out: outId, in: inId });
    }
    savePlan(); renderTeam(); renderPlayers();
  }
  function undoTransfer(inId) {
    const s = P && P.swaps.find((x) => x.in === inId);
    if (s) applyTransfer(inId, s.out);
  }
  function validXI(list) {
    const n = { 1: 0, 2: 0, 3: 0, 4: 0 };
    list.filter((x) => x.slot <= 11).forEach((x) => { n[byId.get(x.id).pos]++; });
    return n[1] === 1 && n[2] >= 3 && n[2] <= 5 && n[3] >= 2 && n[3] <= 5 && n[4] >= 1 && n[4] <= 3 && list.filter((x) => x.slot <= 11).length === 11;
  }
  function swapSlots(aId, bId) {
    startPlan();
    const a = P.picks.find((x) => x.id === aId), b = P.picks.find((x) => x.id === bId);
    const pa = byId.get(aId).pos, pb = byId.get(bId).pos;
    if ((pa === 1) !== (pb === 1)) return 'Goalkeepers can only swap with goalkeepers.';
    const trial = P.picks.map((x) => ({ ...x }));
    const ta = trial.find((x) => x.id === aId), tb = trial.find((x) => x.id === bId);
    [ta.slot, tb.slot] = [tb.slot, ta.slot];
    if (!validXI(trial)) return 'That leaves an invalid formation (1 GKP, 3–5 DEF, 2–5 MID, 1–3 FWD).';
    [a.slot, b.slot] = [b.slot, a.slot];
    for (const x of P.picks) { if (x.slot > 11) { x.c = false; x.vc = false; } }
    ensureCaptain();
    savePlan(); renderTeam();
    return null;
  }
  function ensureCaptain() {
    const xi = P.picks.filter((x) => x.slot <= 11);
    if (!xi.some((x) => x.c)) { const best = [...xi].sort((a, c) => byId.get(c.id).xp1 - byId.get(a.id).xp1)[0]; best.c = true; }
    if (!xi.some((x) => x.vc)) { const best = [...xi].filter((x) => !x.c).sort((a, c) => byId.get(c.id).xp1 - byId.get(a.id).xp1)[0]; if (best) best.vc = true; }
  }
  function setCaptain(id, vice) {
    startPlan();
    const pk = P.picks.find((x) => x.id === id);
    if (!pk || pk.slot > 11) return;
    if (vice) { if (pk.c) return; P.picks.forEach((x) => { x.vc = false; }); pk.vc = true; }
    else { const oldC = P.picks.find((x) => x.c); P.picks.forEach((x) => { x.c = false; }); pk.c = true; if (pk.vc) { pk.vc = false; if (oldC && oldC.id !== id) oldC.vc = true; } P.picks.forEach((x) => { if (x.vc && x.c) x.vc = false; }); }
    ensureCaptain();
    savePlan(); renderTeam();
  }
  function autoXI() {
    startPlan();
    const best = bestXI(picks());
    if (!best) return;
    const ids = new Set(best.xi.map((x) => x.id));
    const xi = best.xi.map((x) => x.p), benchOut = picks().filter((x) => !ids.has(x.id)).map((x) => x.p);
    const order = [...xi.filter((p) => p.pos === 1), ...xi.filter((p) => p.pos === 2), ...xi.filter((p) => p.pos === 3), ...xi.filter((p) => p.pos === 4)];
    const benchOrder = [...benchOut.filter((p) => p.pos === 1), ...benchOut.filter((p) => p.pos !== 1).sort((a, c) => c.xp1 - a.xp1)];
    const sorted = [...xi].sort((a, c) => c.xp1 - a.xp1);
    P.picks = [...order, ...benchOrder].map((p, i) => ({ id: p.id, slot: i + 1, mult: i < 11 ? 1 : 0, c: p.id === sorted[0].id, vc: p.id === sorted[1].id }));
    savePlan(); renderTeam();
  }
  function planXP() {
    const list = picks();
    const xi = list.filter((x) => x.slot <= 11);
    let t = xi.reduce((s, x) => s + x.p.xp1, 0);
    const cap = xi.find((x) => x.c);
    if (cap) t += cap.p.xp1 * (P && P.chip === '3xc' ? 2 : 1);
    if (P && P.chip === 'bboost') t += list.filter((x) => x.slot > 11).reduce((s, x) => s + x.p.xp1, 0);
    return t;
  }

  // ---------- data ----------
  async function load() {
    try {
      const r = await fetch('data/fpl.json?ts=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      D = await r.json();
    } catch (e) {
      const st = $('#status');
      st.textContent = 'Could not load data/fpl.json (' + e.message + '). Run the "Update FPL data" workflow in GitHub Actions, then reload.';
      st.classList.remove('hidden');
      $('#gw-label').textContent = 'No data';
      return;
    }
    byId = new Map(D.players.map((p) => [p.id, p]));
    loadPlan();
    if (P && D.me && P.base_gw !== D.me.picks_gw) {
      // FPL has confirmed a newer squad; the plan was for an earlier deadline.
      P = null; savePlan();
      note(`FPL now shows your confirmed GW${D.me.picks_gw} squad, so the earlier plan was cleared.`);
    }
    if (P) P.picks = P.picks.filter((x) => byId.has(x.id));
    startPlan();
    renderHeader();
    renderTeam();
    renderPlayers();
    renderFixtures();
    showView(S.view);
    $('#generated').textContent = 'Data refreshed ' + relTime(D.generated) + ' (' + fmtDate(D.generated) + ').';
  }
  function note(msg) { const st = $('#status'); st.textContent = msg; st.classList.remove('hidden'); }

  // ---------- helpers ----------
  function fmtDate(iso) {
    if (!iso) return '-';
    return new Date(iso).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  function relTime(iso) {
    const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (m < 60) return m + ' min ago';
    const h = Math.round(m / 60);
    if (h < 48) return h + ' h ago';
    return Math.round(h / 24) + ' days ago';
  }
  function countdown(iso) {
    const ms = new Date(iso).getTime() - Date.now();
    if (ms <= 0) return 'deadline passed';
    const d = Math.floor(ms / 86400000), h = Math.floor((ms % 86400000) / 3600000), m = Math.floor((ms % 3600000) / 60000);
    return (d ? d + 'd ' : '') + h + 'h ' + m + 'm';
  }
  const teamOf = (p) => D.teams[p.team];
  function fxChip(f) {
    if (!f) return '<span class="fx blank">-</span>';
    return `<span class="fx fdr-${f.fdr}" title="${esc(fmtDate(f.kickoff))}">${esc(f.opp_short)}${f.home ? ' (H)' : ' (A)'}</span>`;
  }
  function nextFixtures(p, n) {
    const t = teamOf(p), out = [];
    for (let i = 0; i < n; i++) { const gw = t.fixtures[i] || []; out.push(gw.length ? gw.map(fxChip).join('') : fxChip(null)); }
    return out;
  }
  function flag(p) {
    if (p.status === 'a') return '';
    return `<span class="${p.status === 'd' ? 'warn' : 'bad'}" title="${esc(p.news)}">${esc(STATUS[p.status] || p.status)}${p.chance != null ? ' ' + p.chance + '%' : ''}</span>`;
  }

  // ---------- header ----------
  function renderHeader() {
    $('#season').textContent = D.season;
    if (D.next_gw && D.deadline) {
      $('#gw-label').textContent = `GW${D.next_gw} deadline ${fmtDate(D.deadline)}`;
      const tick = () => { $('#countdown').textContent = countdown(D.deadline); };
      tick(); setInterval(tick, 30000);
    } else $('#gw-label').textContent = 'Season over';
  }

  // ---------- tabs ----------
  function showView(v) {
    S.view = v; persist();
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === v));
    document.querySelectorAll('.view').forEach((s) => s.classList.toggle('hidden', s.id !== 'view-' + v));
  }
  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => showView(t.dataset.view)));
  $('#gw-reset').addEventListener('click', () => { if (confirm('Discard all planned changes and go back to the confirmed squad?')) resetPlan(); });
  $('#gw-auto').addEventListener('click', () => autoXI());

  // ---------- my team ----------
  function renderTeam() {
    const me = D.me;
    if (!me || !me.picks || !me.picks.length) { $('#team-empty').classList.remove('hidden'); return; }
    $('#team-content').classList.remove('hidden');
    startPlan();
    const ch = planChanges();

    // Last gameweek, one quiet line.
    $('#lastgw').innerHTML = `<b>${esc(me.team_name || 'My team')}</b> · GW${me.picks_gw}: <b>${me.gw_points ?? '-'}</b> pts · total ${commas(me.overall_points)} · rank ${commas(me.overall_rank)} · value ${money(me.value)}${me.active_chip ? ' · ' + (CHIP_NAME[me.active_chip] || me.active_chip) : ''}`;

    // The upcoming gameweek card.
    $('#gw-title').textContent = `GW${D.next_gw} squad`;
    const ft = me.free_transfers, used = P.swaps.length, h = hit();
    let ftTile;
    if (P.chip === 'wildcard' || P.chip === 'freehit') ftTile = `${used}<span class="sub2">free on ${CHIP_LABEL[P.chip]}</span>`;
    else if (ft == null) ftTile = `${used}<span class="sub2">used · FT unknown</span>`;
    else ftTile = `<span class="${used > ft ? 'bad' : ''}">${Math.max(0, ft - used)}</span><span class="sub2">of ${ft} free left${h ? ` · <span class="bad">-${h} pts</span>` : ''}</span>`;
    $('#gw-stats').innerHTML = [
      ['Transfers', ftTile], ['Bank', `<span class="${bank() < 0 ? 'bad' : ''}">${money(bank())}</span>`], ['xP', `${num(planXP() - h)}<span class="sub2">${P.chip ? CHIP_NAME[P.chip] : 'no chip'}</span>`],
    ].map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
    $('#gw-reset').classList.toggle('hidden', !ch.any);
    $('#gw-hint').textContent = pendingSwap ? 'Tap another player to swap positions.' : ch.any ? '' : 'Tap a player to transfer, captain or swap. This starts from your confirmed squad.';

    const list = picks();
    const xi = list.filter((x) => x.slot <= 11), bench = list.filter((x) => x.slot > 11).sort((a, c) => a.slot - c.slot);
    const pitch = $('#pitch'); pitch.innerHTML = '';
    for (const pos of [1, 2, 3, 4]) {
      const row = el('div', 'row');
      xi.filter((x) => x.p.pos === pos).forEach((x) => row.appendChild(pcard(x, ch)));
      pitch.appendChild(row);
    }
    const b = $('#bench'); b.innerHTML = '';
    bench.forEach((x) => b.appendChild(pcard(x, ch)));

    renderChanges(ch);
    renderConfirmed(me);
    renderChips(me);
    renderBestXI(list);
    renderOutlook(list);
    renderTransfers(list);
    renderHistory(me);
  }
  // Plain list of how the plan differs from the confirmed squad.
  function renderChanges(ch) {
    const box = $('#changes'); box.innerHTML = '';
    const nm = (id) => esc((byId.get(id) || {}).name || '?');
    if (!ch.any) { box.innerHTML = `<p class="muted small">No changes yet. This is your confirmed GW${D.me.picks_gw} squad carried into GW${D.next_gw}.</p>`; return; }
    const ul = el('div', 'list');
    for (const s of ch.swaps) {
      const o = byId.get(s.out), i = byId.get(s.in);
      const d = xpN(i, xh()) - xpN(o, xh());
      const item = el('div', 'item', `<div class="l"><span class="tag">OUT</span> ${esc(o.name)} <span class="muted">${money(o.price)}</span><span class="arrow">→</span><span class="tag in">IN</span> <b>${esc(i.name)}</b> <span class="muted">${esc(teamOf(i).short)} ${money(i.price)}</span></div><div class="r"><span class="${d >= 0 ? 'good' : 'bad'}">${d >= 0 ? '+' : ''}${num(d)} xP${xh()}</span> <button class="btn small">Undo</button></div>`);
      item.querySelector('button').addEventListener('click', (e) => { e.stopPropagation(); undoTransfer(i.id); });
      ul.appendChild(item);
    }
    ch.starts.forEach((id, k) => ul.appendChild(el('div', 'item', `<div class="l"><span class="tag">XI</span> <b>${nm(id)}</b> starts${ch.benched[k] ? ` for ${nm(ch.benched[k])}` : ''}</div>`)));
    if (ch.captain) ul.appendChild(el('div', 'item', `<div class="l"><span class="tag">C</span> Captain <b>${nm(ch.captain.to)}</b> <span class="muted">was ${nm(ch.captain.from)}</span></div>`));
    if (ch.vice) ul.appendChild(el('div', 'item', `<div class="l"><span class="tag">V</span> Vice <b>${nm(ch.vice.to)}</b> <span class="muted">was ${nm(ch.vice.from)}</span></div>`));
    if (ch.chip) ul.appendChild(el('div', 'item', `<div class="l"><span class="tag">CHIP</span> <b>${CHIP_NAME[ch.chip]}</b></div>`));
    box.appendChild(ul);
    box.appendChild(el('p', 'muted small', 'These are notes for you. Make the moves in the FPL app before the deadline.'));
  }
  // The confirmed squad as a compact reference list.
  function renderConfirmed(me) {
    $('#confirmed-title').textContent = `Confirmed GW${me.picks_gw} squad`;
    const rows = me.picks.map((pk) => ({ ...pk, p: byId.get(pk.id) })).filter((x) => x.p).sort((a, c) => a.slot - c.slot);
    $('#confirmed').innerHTML = `<table class="data"><thead><tr><th>#</th><th>Player</th><th>Pos</th><th>Team</th><th>£</th><th>GW${me.picks_gw} pts</th></tr></thead><tbody>${rows.map((x) => `<tr class="${x.slot > 11 ? 'benchrow' : ''}"><td>${x.slot > 11 ? 'B' + (x.slot - 11) : x.slot}</td><td class="name">${esc(x.p.name)}${x.c ? ' <b>(C)</b>' : x.vc ? ' <span class="muted">(V)</span>' : ''}</td><td>${POS[x.p.pos]}</td><td>${esc(teamOf(x.p).short)}</td><td>${num(x.p.price)}</td><td>${x.p.ev_pts != null ? x.p.ev_pts * (x.mult || 1) : '-'}</td></tr>`).join('')}</tbody></table>`;
  }
  // Per-gameweek xP for the squad over the chosen horizon.
  function renderOutlook(list) {
    const n = xh();
    $('#outlook-note').textContent = `next ${n} GW${n === 1 ? '' : 's'}`;
    const seg = $('#f-xh-team');
    seg.innerHTML = [1, 3, 5, 8].map((k) => `<button data-n="${k}" class="${k === n ? 'active' : ''}">${k} GW${k === 1 ? '' : 's'}</button>`).join('');
    seg.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { S.xh = Number(b.dataset.n); persist(); renderTeam(); renderPlayers(); }));
    const rows = [...list].sort((a, c) => xpN(c.p, n) - xpN(a.p, n));
    const head = ['Player', 'xP' + n].concat(Array.from({ length: n }, (_, i) => 'GW' + (D.next_gw + i)));
    const body = rows.map((x) => {
      const p = x.p, t = teamOf(p);
      const cells = Array.from({ length: n }, (_, i) => {
        const g = t.fixtures[i] || [];
        return `<td>${g.length ? g.map(fxChip).join('') : fxChip(null)}<span class="sub x">${num(p.xp_gw[i])}</span></td>`;
      }).join('');
      return `<tr class="clickable${x.slot > 11 ? ' benchrow' : ''}" data-id="${p.id}"><td class="name">${esc(p.name)}${x.c ? ' <b>(C)</b>' : x.vc ? ' <span class="muted">(V)</span>' : ''} ${flag(p)}<span class="sub">${esc(t.short)} · ${POS[p.pos]}${x.slot > 11 ? ' · bench' : ''}</span></td><td class="x">${num(xpN(p, n))}</td>${cells}</tr>`;
    }).join('');
    $('#outlook').innerHTML = `<table class="data outlook"><thead><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>`;
    $('#outlook').querySelectorAll('tr[data-id]').forEach((tr) => tr.addEventListener('click', () => openPlayer(byId.get(Number(tr.dataset.id)))));
  }
  function availableChips() {
    const used = D.me.chips_used || [];
    return (D.chips || []).filter((c) => D.next_gw >= c.start && D.next_gw <= c.stop && !used.some((u) => u.name === c.name && u.gw >= c.start && u.gw <= c.stop));
  }
  function pcard(x, ch) {
    const p = x.p, t = teamOf(p);
    const isIn = ch && ch.swaps.some((s) => s.in === p.id);
    const moved = ch && (ch.starts.includes(p.id) || ch.benched.includes(p.id));
    const c = el('div', 'pcard flag-' + p.status + (isIn ? ' planned' : '') + (pendingSwap === p.id ? ' selected' : ''));
    c.innerHTML = `${x.c ? '<span class="badge">C</span>' : x.vc ? '<span class="badge vc">V</span>' : ''}
      ${isIn ? '<span class="pill">NEW</span>' : moved ? `<span class="pill move">${x.slot <= 11 ? '↑ XI' : '↓ bench'}</span>` : ''}
      <div class="n">${esc(p.name)}</div>
      <div class="t">${esc(t.short)} ${money(p.price)}</div>
      <div>${nextFixtures(p, 1)[0]}</div>
      <div class="x">${num(p.xp1)} xP</div>
      <div class="small">${flag(p)}</div>`;
    c.addEventListener('click', () => {
      if (pendingSwap && pendingSwap !== p.id) {
        const from = pendingSwap;
        pendingSwap = null;
        const err = swapSlots(from, p.id);
        if (err) note(err); else $('#status').classList.add('hidden');
        renderTeam();
        return;
      }
      openPlayer(p, x);
    });
    return c;
  }
  function renderChips(me) {
    const box = $('#chips'); box.innerHTML = '';
    const used = me.chips_used || [];
    const avail = new Set(availableChips().map((c) => c.name));
    // Sets are grouped by the window's end: wildcard and free hit open at GW2
    // while the other chips open at GW1, but every chip in a set closes together.
    const stops = [...new Set((D.chips || []).map((c) => c.stop))].sort((a, c) => a - c);
    const defs = [...(D.chips || [])].sort((a, c) => (a.stop - c.stop) || (CHIP_ORDER.indexOf(a.name) - CHIP_ORDER.indexOf(c.name)));
    for (const c of defs) {
      const set = stops.indexOf(c.stop) + 1;
      const u = used.find((x) => x.name === c.name && x.gw >= c.start && x.gw <= c.stop);
      const future = D.next_gw && D.next_gw < c.start;
      const current = D.next_gw >= c.start && D.next_gw <= c.stop;
      const plannedNow = P && P.chip === c.name && current;
      const tappable = current && avail.has(c.name);
      const div = el('div', 'chip' + (u ? ' used' : future ? ' future' : '') + (plannedNow ? ' planned' : '') + (tappable ? ' tappable' : ''));
      div.innerHTML = `<div class="k">${CHIP_LABEL[c.name] || c.name}${set}</div><div class="small muted">${u ? 'GW' + u.gw : plannedNow ? 'planned GW' + D.next_gw : 'GW' + c.start + '–' + c.stop}</div>`;
      div.title = CHIP_NAME[c.name] || c.name;
      if (tappable) div.addEventListener('click', () => {
        P.chip = P.chip === c.name ? null : c.name;
        savePlan(); renderTeam();
      });
      box.appendChild(div);
    }
    if (!defs.length) box.innerHTML = '<span class="muted small">Chip definitions not in data.</span>';
    $('#chips-note').textContent = P && P.chip ? `${CHIP_NAME[P.chip]} planned for GW${D.next_gw} · tap to clear` : `tap a chip to plan it for GW${D.next_gw}`;
  }
  function bestXI(list) {
    const by = { 1: [], 2: [], 3: [], 4: [] };
    list.forEach((x) => by[x.p.pos].push(x));
    for (const k in by) by[k].sort((a, c) => c.p.xp1 - a.p.xp1);
    let best = null;
    for (let d = 3; d <= 5; d++) for (let m = 2; m <= 5; m++) {
      const f = 10 - d - m;
      if (f < 1 || f > 3) continue;
      if (by[2].length < d || by[3].length < m || by[4].length < f || by[1].length < 1) continue;
      const xi = [by[1][0], ...by[2].slice(0, d), ...by[3].slice(0, m), ...by[4].slice(0, f)];
      const sum = xi.reduce((s, x) => s + x.p.xp1, 0);
      if (!best || sum > best.sum) best = { xi, sum, shape: `${d}-${m}-${f}` };
    }
    return best;
  }
  function renderBestXI(list) {
    const box = $('#bestxi'); box.innerHTML = '';
    const best = bestXI(list);
    if (!best) { box.textContent = 'Squad does not fit a valid formation.'; return; }
    const cur = list.filter((x) => x.slot <= 11);
    const curSum = cur.reduce((s, x) => s + x.p.xp1, 0);
    const sorted = [...best.xi].sort((a, c) => c.p.xp1 - a.p.xp1);
    const cap = sorted[0], vc = sorted[1];
    const curCap = list.find((x) => x.c);
    $('#bestxi-note').textContent = `${best.shape} · ${num(best.sum)} xP vs ${num(curSum)} as planned`;
    const ids = new Set(best.xi.map((x) => x.id));
    const outs = cur.filter((x) => !ids.has(x.id)), ins = best.xi.filter((x) => !cur.some((c) => c.id === x.id));
    const ul = el('div', 'list');
    ins.forEach((x, i) => {
      const o = outs[i];
      ul.appendChild(el('div', 'item', `<div class="l">Start <b>${esc(x.p.name)}</b> (${num(x.p.xp1)})${o ? ` for ${esc(o.p.name)} (${num(o.p.xp1)})` : ''}</div>`));
    });
    ul.appendChild(el('div', 'item', `<div class="l">Captain <b>${esc(cap.p.name)}</b> (${num(cap.p.xp1)}), vice ${esc(vc.p.name)} (${num(vc.p.xp1)})${curCap && curCap.id !== cap.id ? ` · currently ${esc(curCap.p.name)}` : ' · as now'}</div>`));
    if (!ins.length && curCap && curCap.id === cap.id) ul.prepend(el('div', 'item', '<div class="l">Your XI and captain are already the best by xP.</div>'));
    else { const b = el('button', 'btn', 'Apply to plan'); b.addEventListener('click', autoXI); ul.appendChild(b); }
    box.appendChild(ul);
  }
  function renderTransfers(list) {
    const box = $('#transfers'); box.innerHTML = '';
    const ideas = [];
    for (const x of list) {
      for (const q of candidates(x.p)) {
        if (!fit(q)) continue;
        const delta = xpN(q, xh()) - xpN(x.p, xh());
        if (delta > 1) ideas.push({ out: x.p, in: q, delta, bank: bank() + x.p.price - q.price });
      }
    }
    ideas.sort((a, c) => c.delta - a.delta);
    const seenOut = new Set(), seenIn = new Set(); const top = [];
    for (const i of ideas) {
      if (seenOut.has(i.out.id) || seenIn.has(i.in.id)) continue;
      seenOut.add(i.out.id); seenIn.add(i.in.id); top.push(i);
      if (top.length === 8) break;
    }
    $('#transfers-note').textContent = `one-for-one, within budget, next ${xh()} GW${xh() === 1 ? '' : 's'}`;
    if (!top.length) { box.innerHTML = `<p class="muted small">No one-for-one swap gains more than 1 xP over the next ${xh()} gameweek${xh() === 1 ? '' : 's'}.</p>`; return; }
    const ul = el('div', 'list');
    for (const i of top) {
      const it = el('div', 'item', `<div class="l"><span class="muted">${POS[i.out.pos]}</span> ${esc(i.out.name)} <span class="muted">${num(xpN(i.out, xh()))}</span><span class="arrow">→</span><b>${esc(i.in.name)}</b> <span class="muted">${esc(teamOf(i.in).short)} ${money(i.in.price)}</span></div><div class="r"><span class="good">+${num(i.delta)} xP</span><br><span class="muted small">bank ${money(i.bank)}</span> <button class="btn small">Add</button></div>`);
      it.querySelector('button').addEventListener('click', (e) => { e.stopPropagation(); applyTransfer(i.out.id, i.in.id); });
      it.addEventListener('click', () => openPlayer(i.in));
      ul.appendChild(it);
    }
    box.appendChild(ul);
    const ft = D.me.free_transfers;
    box.appendChild(el('p', 'muted small', `Assumes selling at current price. ${ft != null ? ft + ' free transfer' + (ft === 1 ? '' : 's') + ' available; each extra costs 4 points.' : ''}`));
  }
  function renderHistory(me) {
    const h = me.history || [];
    if (!h.length) { $('#history').innerHTML = '<p class="muted small" style="padding:8px">No gameweeks played yet.</p>'; return; }
    const avg = h.reduce((s, r) => s + r.pts, 0) / h.length;
    $('#season-note').textContent = `avg ${num(avg)} pts/GW`;
    const rows = h.map((r) => `<tr><td>GW${r.gw}</td><td>${r.pts}</td><td>${commas(r.rank)}</td><td>${r.transfers}${r.hit ? ` <span class="bad">(-${r.hit})</span>` : ''}</td><td>${r.bench}</td><td>${money(r.value)}</td></tr>`).join('');
    $('#history').innerHTML = `<table class="data"><thead><tr><th>GW</th><th>Pts</th><th>Rank</th><th>Tr</th><th>Bench</th><th>Value</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  // ---------- players ----------
  const BASE_COLS = [
    { k: 'name', l: 'Player', str: true }, { k: 'price', l: '£', f: (v) => num(v, 1) }, { k: 'sel', l: 'Sel%', f: (v) => num(v, 1) },
    { k: 'form', l: 'Form' }, { k: 'pts', l: 'Pts', f: (v) => v }, { k: 'min', l: 'Min', f: (v) => v },
    { k: 'xg', l: 'xG' }, { k: 'xa', l: 'xA' }, { k: 'xgi90', l: 'xGI/90', f: (v) => num(v, 2) }, { k: 'dc90', l: 'DC/90' },
    { k: 'ep_next', l: 'FPL ep' },
  ];
  let COLS = [];
  function buildCols() {
    const n = xh();
    COLS = [...BASE_COLS, { k: 'xpn', l: 'xP' + n, x: true, v: (p) => xpN(p, n) }];
    for (let i = 0; i < n; i++) COLS.push({ k: 'gw' + i, l: 'GW' + (D.next_gw + i), gw: i, v: (p) => p.xp_gw[i] || 0 });
  }
  const colVal = (c, p) => (c.v ? c.v(p) : p[c.k]);
  function renderPlayers() {
    buildCols();
    document.querySelectorAll('#f-xh button').forEach((b) => b.classList.toggle('active', Number(b.dataset.n) === S.xh));
    const sel = $('#f-team');
    if (sel.options.length === 1) Object.values(D.teams).sort((a, c) => a.name.localeCompare(c.name)).forEach((t) => sel.appendChild(new Option(t.name, t.id)));
    sel.value = S.team; $('#f-price').value = S.price; $('#f-avail').checked = S.avail; $('#f-mine').checked = S.mine; $('#f-search').value = S.search;
    document.querySelectorAll('#f-pos button').forEach((b) => b.classList.toggle('active', Number(b.dataset.pos) === S.pos));
    const thead = $('#players-table thead');
    thead.innerHTML = '<tr>' + COLS.map((c) => `<th data-k="${c.k}" class="${S.sort === c.k ? 'sorted' : ''}">${c.l}${S.sort === c.k ? (S.dir < 0 ? ' ▼' : ' ▲') : ''}</th>`).join('') + '</tr>';
    thead.querySelectorAll('th').forEach((th) => th.addEventListener('click', () => {
      const c = COLS.find((x) => x.k === th.dataset.k); if (c.nosort) return;
      if (S.sort === c.k) S.dir = -S.dir; else { S.sort = c.k; S.dir = c.str ? 1 : -1; }
      persist(); renderPlayers();
    }));
    const mine = D.me && D.me.picks.length ? squadIds() : new Set();
    const q = S.search.trim().toLowerCase();
    const rows = D.players.filter((p) => (!S.pos || p.pos === S.pos) && (!S.team || p.team === Number(S.team))
      && (!S.price || p.price <= Number(S.price)) && (!S.avail || fit(p))
      && (!S.mine || !mine.has(p.id)) && (!q || p.name.toLowerCase().includes(q) || p.full.toLowerCase().includes(q)));
    const sc = COLS.find((c) => c.k === S.sort) || COLS.find((c) => c.k === 'xpn');
    rows.sort((a, c) => { const x = colVal(sc, a), y = colVal(sc, c); return (x > y ? 1 : x < y ? -1 : 0) * S.dir; });
    const limit = 200;
    const tb = $('#players-table tbody'); tb.innerHTML = '';
    for (const p of rows.slice(0, limit)) {
      const tr = el('tr', 'clickable' + (mine.has(p.id) ? ' mine' : ''));
      tr.innerHTML = COLS.map((c) => {
        if (c.k === 'name') return `<td class="name">${esc(p.name)} ${flag(p)}<span class="sub">${esc(teamOf(p).short)} · ${POS[p.pos]}</span></td>`;
        if (c.gw != null) { const g = teamOf(p).fixtures[c.gw] || []; return `<td>${g.length ? g.map(fxChip).join('') : fxChip(null)}<span class="sub x">${num(p.xp_gw[c.gw])}</span></td>`; }
        return `<td class="${c.x ? 'x' : ''}">${c.f ? c.f(p[c.k]) : num(colVal(c, p))}</td>`;
      }).join('');
      tr.addEventListener('click', () => openPlayer(p));
      tb.appendChild(tr);
    }
    $('#players-count').textContent = rows.length > limit ? `Showing ${limit} of ${rows.length} players; narrow the filters to see the rest.` : `${rows.length} players`;
  }
  $('#f-search').addEventListener('input', (e) => { S.search = e.target.value; persist(); renderPlayers(); });
  $('#f-team').addEventListener('change', (e) => { S.team = Number(e.target.value); persist(); renderPlayers(); });
  $('#f-price').addEventListener('input', (e) => { S.price = e.target.value; persist(); renderPlayers(); });
  $('#f-avail').addEventListener('change', (e) => { S.avail = e.target.checked; persist(); renderPlayers(); });
  $('#f-mine').addEventListener('change', (e) => { S.mine = e.target.checked; persist(); renderPlayers(); });
  document.querySelectorAll('#f-pos button').forEach((b) => b.addEventListener('click', () => { S.pos = Number(b.dataset.pos); persist(); renderPlayers(); }));
  document.querySelectorAll('#f-xh button').forEach((b) => b.addEventListener('click', () => {
    S.xh = Number(b.dataset.n);
    if (/^gw\d+$/.test(S.sort) && Number(S.sort.slice(2)) >= S.xh) S.sort = 'xpn';
    persist(); renderPlayers(); if (D.me && D.me.picks.length) renderTeam();
  }));

  // ---------- fixtures ----------
  function renderFixtures() {
    document.querySelectorAll('#f-horizon button').forEach((b) => b.classList.toggle('active', Number(b.dataset.n) === S.horizon));
    const n = S.horizon;
    const teams = Object.values(D.teams).map((t) => {
      let score = 0;
      for (let i = 0; i < n; i++) { const g = t.fixtures[i] || []; score += g.length ? g.reduce((s, f) => s + f.fdr, 0) / g.length - (g.length > 1 ? 1 : 0) : 4.5; }
      return { t, score };
    }).sort((a, c) => a.score - c.score);
    const head = ['Team', 'Diff'];
    for (let i = 0; i < n; i++) head.push('GW' + (D.next_gw + i));
    $('#fixtures-table thead').innerHTML = '<tr>' + head.map((h) => `<th>${h}</th>`).join('') + '</tr>';
    const tb = $('#fixtures-table tbody'); tb.innerHTML = '';
    for (const { t, score } of teams) {
      const tds = [`<td>${esc(t.short)}<span class="sub">${esc(t.name)}</span></td>`, `<td>${num(score)}</td>`];
      for (let i = 0; i < n; i++) { const g = t.fixtures[i] || []; tds.push(`<td>${g.length ? g.map(fxChip).join('') : fxChip(null)}</td>`); }
      tb.appendChild(el('tr', null, tds.join('')));
    }
  }
  document.querySelectorAll('#f-horizon button').forEach((b) => b.addEventListener('click', () => { S.horizon = Number(b.dataset.n); persist(); renderFixtures(); }));

  // ---------- player sheet ----------
  function openModal(html) { $('#modal-content').innerHTML = html; $('#modal').classList.remove('hidden'); }
  function closeModal() { $('#modal').classList.add('hidden'); }
  function openPlayer(p, pick) {
    const t = teamOf(p);
    const parts = p.parts || {};
    const total = Object.values(parts).reduce((s, v) => s + Math.max(v, 0), 0) || 1;
    const colors = { app: '#64748b', att: '#38bdf8', cs: '#4ade80', dc: '#a78bfa', sv: '#fbbf24', bon: '#f472b6' };
    const labels = { app: 'Appearance', att: 'Goals & assists', cs: 'Clean sheet net', dc: 'DefCon', sv: 'Saves', bon: 'Bonus' };
    const bar = Object.keys(parts).filter((k) => parts[k] > 0).map((k) => `<span style="width:${(parts[k] / total * 100).toFixed(1)}%;background:${colors[k]}" title="${labels[k]} ${num(parts[k])}"></span>`).join('');
    const legend = Object.keys(parts).map((k) => `<span class="small"><span style="color:${colors[k]}">■</span> ${labels[k]} ${num(parts[k])}</span>`).join(' &nbsp; ');
    const fx = (t.fixtures || []).map((g, i) => `<span class="small muted">GW${D.next_gw + i}</span> ${g.length ? g.map(fxChip).join('') : fxChip(null)} <span class="small muted">${num(p.xp_gw[i])}</span>`).join('<br>');

    const inSquad = D.me && D.me.picks.length && squadIds().has(p.id);
    let actions = '';
    if (D.me && D.me.picks.length) {
      if (inSquad) {
        const pk = pick || picks().find((x) => x.id === p.id);
        const planned = P && P.swaps.some((s) => s.in === p.id);
        actions = `<div class="actions">
          <button class="btn primary" data-act="out">Transfer out</button>
          ${pk.slot <= 11 ? `<button class="btn" data-act="cap">Captain</button><button class="btn" data-act="vc">Vice</button>` : ''}
          <button class="btn" data-act="swap">Swap position…</button>
          ${planned ? '<button class="btn" data-act="undo">Undo transfer</button>' : ''}
        </div>`;
      } else {
        actions = `<div class="actions"><button class="btn primary" data-act="in">Transfer in…</button></div>`;
      }
    }
    openModal(`
      <h2>${esc(p.full)} <span class="muted">${esc(t.name)} · ${POS[p.pos]} · ${money(p.price)}</span></h2>
      ${p.news ? `<p class="${p.status === 'd' ? 'warn' : 'bad'} small">${esc(p.news)}</p>` : ''}
      ${actions}
      <div class="kv">
        <span class="k">Next GW xP</span><span><b>${num(p.xp1)}</b> (FPL's own ep ${num(p.ep_next)})</span>
        <span class="k">Next 5 xP</span><span><b>${num(p.xp5)}</b></span>
        <span class="k">Chance of playing</span><span>${Math.round(p.p_play * 100)}%${p.chance != null ? ' (FPL flag ' + p.chance + '%)' : ''}</span>
        <span class="k">Season</span><span>${p.pts} pts · ${p.min} min · ${p.g}G ${p.a}A ${p.cs}CS · ${p.bonus} bonus</span>
        <span class="k">Per 90 (raw)</span><span>xGI ${num(p.xgi90, 2)} · xGC ${num(p.xgc90, 2)} · DC ${num(p.dc90)}${p.pos === 1 ? ' · saves ' + num(p.saves90) : ''}</span>
        <span class="k">Per 90 (model)</span><span>xG ${num(p.rates.xg90, 2)} · xA ${num(p.rates.xa90, 2)} · DC ${num(p.rates.dc90)} · bonus/g ${num(p.rates.bon, 2)} <span class="muted">(prior: ${p.rates.src === 'prev' ? 'last season' : 'price'})</span></span>
        <span class="k">Ownership</span><span>${num(p.sel)}% · in ${commas(p.tin)} / out ${commas(p.tout)} this GW${p.dprice ? ' · price ' + (p.dprice > 0 ? '+' : '') + num(p.dprice) : ''}</span>
      </div>
      <div class="bar">${bar}</div><div>${legend}</div>
      <h2 style="margin-top:12px">Fixtures</h2><div style="line-height:1.9">${fx}</div>`);
    $('#modal-content').querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => {
      const act = b.dataset.act;
      if (act === 'out') return openPicker(p);
      if (act === 'in') return openOutChooser(p);
      if (act === 'cap') { setCaptain(p.id, false); closeModal(); showView('team'); return; }
      if (act === 'vc') { setCaptain(p.id, true); closeModal(); showView('team'); return; }
      if (act === 'swap') { startPlan(); pendingSwap = p.id; closeModal(); showView('team'); renderTeam(); return; }
      if (act === 'undo') { undoTransfer(p.id); closeModal(); }
    }));
  }
  // Choose who comes in for an outgoing player.
  function openPicker(outP) {
    startPlan();
    const list = candidates(outP);
    const budget = bank() + outP.price;
    const render = (q) => {
      const rows = list.filter((c) => !q || c.name.toLowerCase().includes(q) || c.full.toLowerCase().includes(q)).slice(0, 60);
      return rows.map((c) => `<div class="item clickable" data-in="${c.id}"><div class="l"><b>${esc(c.name)}</b> ${flag(c)} <span class="muted">${esc(teamOf(c).short)} ${money(c.price)}</span><br><span class="small">${nextFixtures(c, 3).join(' ')}</span></div><div class="r"><span class="x">${num(xpN(c, xh()))} xP${xh()}</span><br><span class="small ${xpN(c, xh()) - xpN(outP, xh()) >= 0 ? 'good' : 'bad'}">${xpN(c, xh()) - xpN(outP, xh()) >= 0 ? '+' : ''}${num(xpN(c, xh()) - xpN(outP, xh()))}</span></div></div>`).join('') || '<p class="muted small">No affordable players match.</p>';
    };
    openModal(`<h2>Replace ${esc(outP.name)} <span class="muted">${POS[outP.pos]} · up to ${money(budget)}</span></h2>
      <input id="pick-search" type="search" placeholder="Search" autocomplete="off" style="width:100%;margin:8px 0">
      <div id="pick-list" class="list">${render('')}</div>`);
    const bind = () => $('#pick-list').querySelectorAll('[data-in]').forEach((r) => r.addEventListener('click', () => { applyTransfer(outP.id, Number(r.dataset.in)); closeModal(); showView('team'); }));
    bind();
    $('#pick-search').addEventListener('input', (e) => { $('#pick-list').innerHTML = render(e.target.value.trim().toLowerCase()); bind(); });
  }
  // Choose who goes out for an incoming player.
  function openOutChooser(inP) {
    startPlan();
    const clubs = clubCount(null);
    const own = picks().filter((x) => x.p.pos === inP.pos).map((x) => {
      const b = bank() + x.p.price - inP.price;
      const clubOk = inP.team === x.p.team || (clubs[inP.team] || 0) < 3;
      return { x, b, ok: b >= -1e-9 && clubOk, why: !clubOk ? 'already 3 from ' + teamOf(inP).short : b < 0 ? 'over budget' : '' };
    });
    openModal(`<h2>Bring in ${esc(inP.name)} <span class="muted">${esc(teamOf(inP).short)} ${money(inP.price)}</span></h2><p class="muted small">Who goes out?</p>
      <div class="list">${own.map((o) => `<div class="item ${o.ok ? 'clickable' : 'disabled'}" data-out="${o.x.id}"><div class="l"><b>${esc(o.x.p.name)}</b> <span class="muted">${money(o.x.p.price)} · ${num(xpN(o.x.p, xh()))} xP${xh()}</span></div><div class="r">${o.ok ? `<span class="${xpN(inP, xh()) - xpN(o.x.p, xh()) >= 0 ? 'good' : 'bad'}">${xpN(inP, xh()) - xpN(o.x.p, xh()) >= 0 ? '+' : ''}${num(xpN(inP, xh()) - xpN(o.x.p, xh()))} xP${xh()}</span><br><span class="muted small">bank ${money(o.b)}</span>` : `<span class="muted small">${o.why}</span>`}</div></div>`).join('')}</div>`);
    $('#modal-content').querySelectorAll('.item.clickable').forEach((r) => r.addEventListener('click', () => { applyTransfer(Number(r.dataset.out), inP.id); closeModal(); showView('team'); }));
  }
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });

  // ---------- refresh on request ----------
  // Triggers the "Update FPL data" workflow through the GitHub API with a
  // fine-grained token kept only in this browser, then waits for the new bundle.
  const tokenKey = 'fplpicker.gh';
  const getToken = () => { try { return localStorage.getItem(tokenKey) || ''; } catch (e) { return ''; } };
  const actionsUrl = `https://github.com/${REPO.owner}/${REPO.name}/actions/workflows/${REPO.workflow}`;
  $('#refresh').addEventListener('click', () => {
    const tok = getToken();
    if (!tok) return openTokenSetup();
    triggerRefresh(tok);
  });
  function openTokenSetup() {
    openModal(`<h2>Refresh data</h2>
      <p class="small">Refreshing runs the "Update FPL data" workflow on GitHub. To trigger it from here, paste a fine-grained personal access token for the <b>${REPO.owner}/${REPO.name}</b> repository with <b>Actions: read and write</b>. It is stored only in this browser.</p>
      <input id="tok" type="password" placeholder="github_pat_…" style="width:100%;margin:8px 0" autocomplete="off">
      <div class="actions"><button class="btn primary" id="tok-save">Save and refresh</button><a class="btn" href="${actionsUrl}" target="_blank" rel="noopener">Open Actions page instead</a></div>
      <p class="muted small">GitHub: Settings, Developer settings, Personal access tokens, Fine-grained tokens. Repository access: only this repo. Permissions: Actions, read and write.</p>`);
    $('#tok-save').addEventListener('click', () => {
      const v = $('#tok').value.trim();
      if (!v) return;
      try { localStorage.setItem(tokenKey, v); } catch (e) { /* ignore */ }
      closeModal(); triggerRefresh(v);
    });
  }
  async function triggerRefresh(tok) {
    const btn = $('#refresh'); btn.disabled = true; btn.textContent = 'Refreshing…';
    const before = D ? D.generated : null;
    try {
      const r = await fetch(`https://api.github.com/repos/${REPO.owner}/${REPO.name}/actions/workflows/${REPO.workflow}/dispatches`, {
        method: 'POST', headers: { Authorization: 'Bearer ' + tok, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref: 'main' }),
      });
      if (r.status === 401 || r.status === 403 || r.status === 404) { try { localStorage.removeItem(tokenKey); } catch (e) { /* ignore */ } throw new Error('GitHub rejected the token (HTTP ' + r.status + '). It was cleared; try again.'); }
      if (!r.ok) throw new Error('HTTP ' + r.status);
    } catch (e) {
      note('Refresh failed: ' + e.message); btn.disabled = false; btn.textContent = 'Refresh data'; return;
    }
    note('Workflow started. The page reloads when new data lands (usually under two minutes).');
    const started = Date.now();
    const poll = async () => {
      try {
        const r = await fetch('data/fpl.json?ts=' + Date.now(), { cache: 'no-store' });
        const j = await r.json();
        if (j.generated !== before) { location.reload(); return; }
      } catch (e) { /* keep polling */ }
      if (Date.now() - started < 5 * 60000) setTimeout(poll, 15000);
      else { note('No new data after five minutes. Check the Actions page for the run.'); btn.disabled = false; btn.textContent = 'Refresh data'; }
    };
    setTimeout(poll, 20000);
  }

  load();
})();
