(() => {
  'use strict';
  const POS = { 1: 'GKP', 2: 'DEF', 3: 'MID', 4: 'FWD' };
  const CHIP_LABEL = { wildcard: 'WC', freehit: 'FH', bboost: 'BB', '3xc': 'TC' };
  const CHIP_NAME = { wildcard: 'Wildcard', freehit: 'Free Hit', bboost: 'Bench Boost', '3xc': 'Triple Captain' };
  const STATUS = { a: '', d: 'Doubt', i: 'Injured', s: 'Suspended', u: 'Unavailable', n: 'Not in squad' };
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d = 1) => (v == null ? '-' : Number(v).toFixed(d));
  const money = (v) => '£' + Number(v).toFixed(1) + 'm';
  const commas = (v) => (v == null ? '-' : Number(v).toLocaleString('en-GB'));

  let D = null;
  let byId = new Map();
  let mine = new Set();
  const defaults = { view: 'team', pos: 0, team: 0, price: '', avail: true, mine: false, search: '', sort: 'xp5', dir: -1, horizon: 5 };
  let S = { ...defaults };
  try { S = { ...defaults, ...JSON.parse(localStorage.getItem('fplpicker') || '{}') }; } catch (e) { /* fresh */ }
  const persist = () => { try { localStorage.setItem('fplpicker', JSON.stringify(S)); } catch (e) { /* ignore */ } };

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
    mine = new Set(((D.me && D.me.picks) || []).map((p) => p.id));
    renderHeader();
    renderTeam();
    renderPlayers();
    renderFixtures();
    showView(S.view);
    $('#generated').textContent = 'Data refreshed ' + relTime(D.generated) + ' (' + fmtDate(D.generated) + ').';
  }

  // ---------- helpers ----------
  function fmtDate(iso) {
    if (!iso) return '-';
    const d = new Date(iso);
    return d.toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  function relTime(iso) {
    const ms = Date.now() - new Date(iso).getTime();
    const m = Math.round(ms / 60000);
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
  function teamOf(p) { return D.teams[p.team]; }
  function fxChip(f, withGw) {
    if (!f) return '<span class="fx blank">-</span>';
    return `<span class="fx fdr-${f.fdr}" title="${esc(fmtDate(f.kickoff))}">${esc(f.opp_short)}${f.home ? ' (H)' : ' (A)'}${withGw ? ' ' + f.gw : ''}</span>`;
  }
  function nextFixtures(p, n) {
    const t = teamOf(p);
    const out = [];
    for (let i = 0; i < n; i++) {
      const gw = t.fixtures[i] || [];
      out.push(gw.length ? gw.map((f) => fxChip(f)).join('') : fxChip(null));
    }
    return out;
  }
  function flag(p) {
    if (p.status === 'a') return '';
    const label = STATUS[p.status] || p.status;
    return `<span class="${p.status === 'd' ? 'warn' : 'bad'}" title="${esc(p.news)}">${esc(label)}${p.chance != null ? ' ' + p.chance + '%' : ''}</span>`;
  }

  // ---------- header ----------
  function renderHeader() {
    $('#season').textContent = D.season;
    if (D.next_gw && D.deadline) {
      $('#gw-label').textContent = `GW${D.next_gw} deadline ${fmtDate(D.deadline)}`;
      const tick = () => { $('#countdown').textContent = countdown(D.deadline); };
      tick(); setInterval(tick, 30000);
    } else {
      $('#gw-label').textContent = 'Season over';
    }
  }

  // ---------- tabs ----------
  function showView(v) {
    S.view = v; persist();
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.view === v));
    document.querySelectorAll('.view').forEach((s) => s.classList.toggle('hidden', s.id !== 'view-' + v));
  }
  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => showView(t.dataset.view)));

  // ---------- my team ----------
  function renderTeam() {
    const me = D.me;
    if (!me || !me.picks || !me.picks.length) { $('#team-empty').classList.remove('hidden'); return; }
    $('#team-content').classList.remove('hidden');
    $('#team-name').textContent = me.team_name || 'My team';
    $('#team-gw').textContent = `Squad from GW${me.picks_gw}${me.active_chip ? ' · ' + (CHIP_NAME[me.active_chip] || me.active_chip) + ' active' : ''}`;

    const stats = [
      ['GW points', me.gw_points ?? '-'], ['Total', commas(me.overall_points)], ['Rank', commas(me.overall_rank)],
      ['Value', money(me.value)], ['Bank', money(me.bank)], ['Free transfers', me.free_transfers ?? '?'],
    ];
    $('#team-summary').innerHTML = stats.map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

    const picks = me.picks.map((pk) => ({ ...pk, p: byId.get(pk.id) })).filter((x) => x.p);
    const xi = picks.filter((x) => x.slot <= 11), bench = picks.filter((x) => x.slot > 11);
    const pitch = $('#pitch'); pitch.innerHTML = '';
    for (const pos of [1, 2, 3, 4]) {
      const row = el('div', 'row');
      xi.filter((x) => x.p.pos === pos).forEach((x) => row.appendChild(pcard(x)));
      pitch.appendChild(row);
    }
    const b = $('#bench'); b.innerHTML = '';
    bench.sort((a, c) => a.slot - c.slot).forEach((x) => b.appendChild(pcard(x)));

    renderChips(me);
    renderBestXI(picks);
    renderTransfers(picks, me);
    renderHistory(me);
  }
  function pcard(x) {
    const p = x.p, t = teamOf(p);
    const c = el('div', 'pcard flag-' + p.status);
    c.innerHTML = `${x.c ? '<span class="badge">C</span>' : x.vc ? '<span class="badge vc">V</span>' : ''}
      <div class="n">${esc(p.name)}</div>
      <div class="t">${esc(t.short)} ${money(p.price)}</div>
      <div>${nextFixtures(p, 1)[0]}</div>
      <div class="x">${num(p.xp1)} xP</div>
      <div class="small">${flag(p)}</div>`;
    c.addEventListener('click', () => openPlayer(p));
    return c;
  }
  function renderChips(me) {
    const box = $('#chips'); box.innerHTML = '';
    const used = me.chips_used || [];
    const defs = D.chips && D.chips.length ? D.chips : [];
    for (const c of defs) {
      const u = used.find((x) => x.name === c.name && x.gw >= c.start && x.gw <= c.stop);
      const future = D.next_gw && D.next_gw < c.start;
      const div = el('div', 'chip' + (u ? ' used' : future ? ' future' : ''));
      div.innerHTML = `<div class="k">${CHIP_LABEL[c.name] || c.name}${c.number}</div><div class="small muted">${u ? 'GW' + u.gw : 'GW' + c.start + '–' + c.stop}</div>`;
      div.title = CHIP_NAME[c.name] || c.name;
      box.appendChild(div);
    }
    if (!defs.length) box.innerHTML = '<span class="muted small">Chip definitions not in data.</span>';
  }
  function bestXI(picks) {
    const by = { 1: [], 2: [], 3: [], 4: [] };
    picks.forEach((x) => by[x.p.pos].push(x));
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
  function renderBestXI(picks) {
    const box = $('#bestxi'); box.innerHTML = '';
    const best = bestXI(picks);
    if (!best) { box.textContent = 'Squad does not fit a valid formation.'; return; }
    const cur = picks.filter((x) => x.slot <= 11);
    const curSum = cur.reduce((s, x) => s + x.p.xp1, 0);
    const sorted = [...best.xi].sort((a, c) => c.p.xp1 - a.p.xp1);
    const cap = sorted[0], vc = sorted[1];
    const curCap = picks.find((x) => x.c);
    $('#bestxi-note').textContent = `${best.shape} · ${num(best.sum)} xP vs ${num(curSum)} now`;
    const ids = new Set(best.xi.map((x) => x.id));
    const outs = cur.filter((x) => !ids.has(x.id)), ins = best.xi.filter((x) => !cur.some((c) => c.id === x.id));
    const list = el('div', 'list');
    ins.forEach((x, i) => {
      const o = outs[i];
      list.appendChild(el('div', 'item', `<div class="l">Start <b>${esc(x.p.name)}</b> (${num(x.p.xp1)})${o ? ` for ${esc(o.p.name)} (${num(o.p.xp1)})` : ''}</div>`));
    });
    list.appendChild(el('div', 'item', `<div class="l">Captain <b>${esc(cap.p.name)}</b> (${num(cap.p.xp1)}), vice ${esc(vc.p.name)} (${num(vc.p.xp1)})${curCap && curCap.id !== cap.id ? ` · currently ${esc(curCap.p.name)}` : ' · as now'}</div>`));
    if (!ins.length) list.prepend(el('div', 'item', '<div class="l">Your XI is already the best by xP.</div>'));
    box.appendChild(list);
  }
  function renderTransfers(picks, me) {
    const box = $('#transfers'); box.innerHTML = '';
    const clubs = {};
    picks.forEach((x) => { clubs[x.p.team] = (clubs[x.p.team] || 0) + 1; });
    const ideas = [];
    for (const x of picks) {
      const p = x.p;
      const budget = p.price + me.bank;
      for (const q of D.players) {
        if (q.pos !== p.pos || mine.has(q.id) || q.price > budget) continue;
        if (q.status !== 'a' && !(q.chance != null && q.chance >= 75)) continue;
        if (q.team !== p.team && (clubs[q.team] || 0) >= 3) continue;
        const delta = q.xp5 - p.xp5;
        if (delta > 1) ideas.push({ out: p, in: q, delta, bank: me.bank + p.price - q.price });
      }
    }
    ideas.sort((a, c) => c.delta - a.delta);
    // one idea per outgoing and per incoming player, up to 8
    const seenOut = new Set(), seenIn = new Set(); const top = [];
    for (const i of ideas) {
      if (seenOut.has(i.out.id) || seenIn.has(i.in.id)) continue;
      seenOut.add(i.out.id); seenIn.add(i.in.id); top.push(i);
      if (top.length === 8) break;
    }
    if (!top.length) { box.innerHTML = '<p class="muted small">No one-for-one swap gains more than 1 xP over five gameweeks.</p>'; return; }
    const list = el('div', 'list');
    for (const i of top) {
      const it = el('div', 'item', `<div class="l"><span class="muted">${POS[i.out.pos]}</span> ${esc(i.out.name)} <span class="muted">${num(i.out.xp5)}</span><span class="arrow">→</span><b>${esc(i.in.name)}</b> <span class="muted">${esc(teamOf(i.in).short)} ${money(i.in.price)}</span></div><div class="r"><span class="good">+${num(i.delta)} xP</span><br><span class="muted small">bank ${money(i.bank)}</span></div>`);
      it.addEventListener('click', () => openPlayer(i.in));
      list.appendChild(it);
    }
    box.appendChild(list);
    box.appendChild(el('p', 'muted small', `Assumes selling at current price. ${me.free_transfers != null ? me.free_transfers + ' free transfer' + (me.free_transfers === 1 ? '' : 's') + ' available; each extra costs 4 points.' : ''}`));
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
  const COLS = [
    { k: 'name', l: 'Player', str: true }, { k: 'price', l: '£', f: (v) => num(v, 1) }, { k: 'sel', l: 'Sel%', f: (v) => num(v, 1) },
    { k: 'form', l: 'Form' }, { k: 'pts', l: 'Pts', f: (v) => v }, { k: 'min', l: 'Min', f: (v) => v },
    { k: 'xg', l: 'xG' }, { k: 'xa', l: 'xA' }, { k: 'xgi90', l: 'xGI/90', f: (v) => num(v, 2) }, { k: 'dc90', l: 'DC/90' },
    { k: 'ep_next', l: 'FPL ep' }, { k: 'xp1', l: 'xP1', x: true }, { k: 'xp5', l: 'xP5', x: true }, { k: 'fx', l: 'Next 5', str: true, nosort: true },
  ];
  function renderPlayers() {
    const sel = $('#f-team');
    if (sel.options.length === 1) {
      Object.values(D.teams).sort((a, c) => a.name.localeCompare(c.name)).forEach((t) => sel.appendChild(new Option(t.name, t.id)));
    }
    sel.value = S.team; $('#f-price').value = S.price; $('#f-avail').checked = S.avail; $('#f-mine').checked = S.mine; $('#f-search').value = S.search;
    document.querySelectorAll('#f-pos button').forEach((b) => b.classList.toggle('active', Number(b.dataset.pos) === S.pos));
    const thead = $('#players-table thead');
    thead.innerHTML = '<tr>' + COLS.map((c) => `<th data-k="${c.k}" class="${S.sort === c.k ? 'sorted' : ''}">${c.l}${S.sort === c.k ? (S.dir < 0 ? ' ▼' : ' ▲') : ''}</th>`).join('') + '</tr>';
    thead.querySelectorAll('th').forEach((th) => th.addEventListener('click', () => {
      const c = COLS.find((x) => x.k === th.dataset.k); if (c.nosort) return;
      if (S.sort === c.k) S.dir = -S.dir; else { S.sort = c.k; S.dir = c.str ? 1 : -1; }
      persist(); renderPlayers();
    }));
    const q = S.search.trim().toLowerCase();
    let rows = D.players.filter((p) => (!S.pos || p.pos === S.pos) && (!S.team || p.team === Number(S.team))
      && (!S.price || p.price <= Number(S.price)) && (!S.avail || p.status === 'a' || (p.chance != null && p.chance >= 75))
      && (!S.mine || !mine.has(p.id)) && (!q || p.name.toLowerCase().includes(q) || p.full.toLowerCase().includes(q)));
    rows.sort((a, c) => { const k = S.sort; return (a[k] > c[k] ? 1 : a[k] < c[k] ? -1 : 0) * S.dir; });
    const limit = 200;
    const tb = $('#players-table tbody'); tb.innerHTML = '';
    for (const p of rows.slice(0, limit)) {
      const tr = el('tr', 'clickable' + (mine.has(p.id) ? ' mine' : ''));
      tr.innerHTML = COLS.map((c) => {
        if (c.k === 'name') return `<td class="name">${esc(p.name)} ${flag(p)}<span class="sub">${esc(teamOf(p).short)} · ${POS[p.pos]}</span></td>`;
        if (c.k === 'fx') return `<td>${nextFixtures(p, 5).join(' ')}</td>`;
        return `<td class="${c.x ? 'x' : ''}">${c.f ? c.f(p[c.k]) : num(p[c.k])}</td>`;
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
      for (let i = 0; i < n; i++) { const g = t.fixtures[i] || []; tds.push(`<td>${g.length ? g.map((f) => fxChip(f)).join('') : fxChip(null)}</td>`); }
      const tr = el('tr', null, tds.join(''));
      tb.appendChild(tr);
    }
  }
  document.querySelectorAll('#f-horizon button').forEach((b) => b.addEventListener('click', () => { S.horizon = Number(b.dataset.n); persist(); renderFixtures(); }));

  // ---------- player modal ----------
  function openPlayer(p) {
    const t = teamOf(p);
    const parts = p.parts || {};
    const total = Object.values(parts).reduce((s, v) => s + Math.max(v, 0), 0) || 1;
    const colors = { app: '#64748b', att: '#38bdf8', cs: '#4ade80', dc: '#a78bfa', sv: '#fbbf24', bon: '#f472b6' };
    const labels = { app: 'Appearance', att: 'Goals & assists', cs: 'Clean sheet net', dc: 'DefCon', sv: 'Saves', bon: 'Bonus' };
    const bar = Object.keys(parts).filter((k) => parts[k] > 0).map((k) => `<span style="width:${(parts[k] / total * 100).toFixed(1)}%;background:${colors[k]}" title="${labels[k]} ${num(parts[k])}"></span>`).join('');
    const legend = Object.keys(parts).map((k) => `<span class="small"><span style="color:${colors[k]}">■</span> ${labels[k]} ${num(parts[k])}</span>`).join(' &nbsp; ');
    const fx = (t.fixtures || []).map((g, i) => `<span class="small muted">GW${D.next_gw + i}</span> ${g.length ? g.map((f) => fxChip(f)).join('') : fxChip(null)} <span class="small muted">${num(p.xp_gw[i])}</span>`).join('<br>');
    $('#modal-content').innerHTML = `
      <h2>${esc(p.full)} <span class="muted">${esc(t.name)} · ${POS[p.pos]} · ${money(p.price)}</span></h2>
      ${p.news ? `<p class="${p.status === 'd' ? 'warn' : 'bad'} small">${esc(p.news)}</p>` : ''}
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
      <h2 style="margin-top:12px">Fixtures</h2><div style="line-height:1.9">${fx}</div>`;
    $('#modal').classList.remove('hidden');
  }
  $('#modal-close').addEventListener('click', () => $('#modal').classList.add('hidden'));
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') $('#modal').classList.add('hidden'); });

  load();
})();
