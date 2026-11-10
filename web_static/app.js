'use strict';
// Read-only preset viewer. Same physics/overlay code as web/app.js, but every data call reads a static JSON/JPG under data/.
const C = 343.0;
const $ = id => document.getElementById(id);
const getJSON = async u => { const r = await fetch(u); if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); };
const S = {index: null, pid: null, proj: null, view: null, res: null, summary: null, selRank: 1, compare: [], heat: null, usedIds: new Set()};
const COLORS = ['#4fc3f7', '#aed581', '#ffb74d', '#f06292', '#ba68c8', '#4db6ac', '#fff176'];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// ---------------------------------------------------------------- physics (same formulas as dopp/physics.py)
function gPosition(t, v, d, t0) { const tau = t - t0; const vr = v * v * tau / Math.sqrt(d * d + (v * tau) ** 2); return C / (C + vr); }
function gDelayed(t, v, d, t0) {
  const tc = t0 - d / C, s = t - tc, a = C * C - v * v;
  const disc = Math.max(C ** 4 * s * s - a * (C * C * s * s - d * d), 0);
  const u = (C * C * s - Math.sqrt(disc)) / a; const r = Math.sqrt(d * d + (v * u) ** 2);
  return C / (C + v * v * u / r);
}
const readouts = (f0, v, d) => ({fpre: f0 * C / (C - v), fpost: f0 * C / (C + v), roc: f0 * v * v / (C * d)});
const fmtUtc = s => s == null ? '—' : new Date(s * 1000).toISOString().replace('T', ' ').slice(0, 19) + 'Z';
const fmtLocal = (s, tz) => { try { return new Date(s * 1000).toLocaleString('en-US', {timeZone: tz, timeZoneName: 'short'}); } catch (e) { return fmtUtc(s); } };
const fmtT = s => { const sg = s < 0 ? '-' : ''; s = Math.abs(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = (s % 60).toFixed(1).padStart(4, '0'); return sg + (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + x; };
const fx = (x, n = 2) => x == null || !isFinite(x) ? '—' : (+x).toFixed(n);
function haversine(a, b, c, d) { const R = 6371008.8, r = Math.PI / 180; const x = Math.sin((c - a) * r / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin((d - b) * r / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(x)); }

// ---------------------------------------------------------------- hash state  #p=<preset>&r=<result>&k=<rank>
function readHash() { const h = new URLSearchParams(location.hash.slice(1)); return {p: h.get('p'), r: h.get('r'), k: +(h.get('k') || 0)}; }
function writeHash() { const h = new URLSearchParams({p: S.pid || ''}); if (S.res) h.set('r', S.res.id); if (S.selRank) h.set('k', S.selRank); history.replaceState(null, '', '#' + h.toString()); }

// ---------------------------------------------------------------- spectrogram tiles
const imgCache = {};
function getImg(file, cb) {
  const u = `data/${S.pid}/${file}`; let o = imgCache[u];
  if (!o) { o = imgCache[u] = {im: new Image(), ok: false, cbs: []}; o.im.onload = () => { o.ok = true; o.cbs.splice(0).forEach(f => f()); }; o.im.onerror = () => console.warn('image failed', u); o.im.src = u; }
  if (o.ok) return o.im; if (cb) o.cbs.push(cb); return null;
}
// Draw view {t0,t1,f0,f1} into ctx (W x H). Uses the overview when it is fine enough, else the native tiles.
function drawSpecRegion(ctx, W, H, v, redraw) {
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
  const imgs = S.proj.images, ov = imgs.find(i => i.level === 'overview'), tiles = imgs.filter(i => i.level === 'native');
  const need = W / (v.t1 - v.t0), ovRes = 2400 / (ov.t1 - ov.t0);
  const list = need <= ovRes * 1.6 || !tiles.length ? [ov] : tiles;
  let missing = 0;
  for (const m of list) {
    const a = Math.max(v.t0, m.t0), b = Math.min(v.t1, m.t1), fa = Math.max(v.f0, m.f0), fb = Math.min(v.f1, m.f1);
    if (b <= a || fb <= fa) continue;
    const im = getImg(m.file, redraw); if (!im) { missing++; continue; }
    const iw = im.naturalWidth, ih = im.naturalHeight;
    const sx = (a - m.t0) / (m.t1 - m.t0) * iw, sw = (b - a) / (m.t1 - m.t0) * iw;
    const sy = (m.f1 - fb) / (m.f1 - m.f0) * ih, sh = (fb - fa) / (m.f1 - m.f0) * ih;
    const dx = (a - v.t0) / (v.t1 - v.t0) * W, dw = (b - a) / (v.t1 - v.t0) * W;
    const dy = (v.f1 - fb) / (v.f1 - v.f0) * H, dh = (fb - fa) / (v.f1 - v.f0) * H;
    ctx.imageSmoothingEnabled = true; ctx.drawImage(im, sx, sy, sw, sh, dx, dy, dw, dh);
  }
  return missing;
}

// ---------------------------------------------------------------- presets
async function init() {
  S.index = await getJSON('data/index.json');
  $('genInfo').textContent = `Exported ${S.index.generated_utc} from app commit ${S.index.app_commit}.`;
  const groups = {}; const top = [];
  for (const p of S.index.presets) { if (p.group) (groups[p.group] ||= []).push(p); else top.push(p); }
  const opt = p => `<option value="${p.id}">${esc(p.name)}${p.status === 'unsolved' ? ' [unsolved]' : ''}</option>`;
  $('projSel').innerHTML = top.map(opt).join('') + Object.entries(groups).map(([g, ps]) => `<optgroup label="${esc(g)}">${ps.map(opt).join('')}</optgroup>`).join('');
  const h = readHash();
  if (location.hash === '#about') showAbout(true);
  const pid = S.index.presets.some(p => p.id === h.p) ? h.p : S.index.presets[0].id;
  $('projSel').value = pid; await openProject(pid, h.r, h.k);
}
let SEQ = 0;   // drop stale async loads when the user switches quickly
async function openProject(pid, rid, rank) {
  const my = ++SEQ; S.res = null; S.proj = null;
  const r = await getJSON(`data/${pid}/project.json`); if (my !== SEQ) return;
  S.pid = pid; S.proj = r; S.summary = null; S.compare = []; S.heat = null;
  const m = r.meta;
  $('projInfo').innerHTML = `${fmtT(m.duration_s)} · recording start ${m.start_utc != null ? esc(fmtLocal(m.start_utc, m.tz || 'UTC')) : 'unknown'}` +
    (m.truth ? ` · ground truth ${m.truth[0].toFixed(5)}, ${m.truth[1].toFixed(5)}` : '') + (m.note ? `<br>${esc(m.note)}` : '');
  const sb = $('statusBanner');
  if (m.status === 'unsolved') { sb.hidden = false; sb.innerHTML = `<b>Unsolved example.</b> ${esc(m.status_note || '')}`; } else sb.hidden = true;
  S.view = {t0: r.spec.t0, t1: r.spec.t1, f0: r.spec.f0, f1: Math.min(r.spec.f1, 1000)}; if (m.view) Object.assign(S.view, m.view);
  $('s_t0').min = r.spec.t0; $('s_t0').max = r.spec.t1;
  setSliders({f0: 120, v: 100, d: 2000, t0: (S.view.t0 + S.view.t1) / 2});
  $('clipSel').innerHTML = r.audio.map((c, i) => `<option value="${i}">${fmtT(c.t0)}–${fmtT(c.t1)}${c.events ? ' (events ' + c.events.map(x => '#' + x).join(', ') + ')' : ' (whole recording)'}</option>`).join('');
  setClip(0);
  const rs = r.results;
  $('resSel').innerHTML = rs.map(x => `<option value="${x.id}">${esc(x.tag || x.mode)} · ${x.mode} · ${x.n_events} ev${x.truth_rank ? ' · truth #' + x.truth_rank : ''}</option>`).join('');
  const pick = (rs.find(x => x.id === rid) || rs.find(x => m.default_tag && x.tag === m.default_tag) || rs.find(x => x.mode === m.default_mode) || rs[0] || {}).id;
  if (pick) { $('resSel').value = pick; await showResult(pick, rank); } else { renderEvents(); drawSpec(); }
}
$('projSel').onchange = e => openProject(e.target.value);
$('resSel').onchange = e => showResult(e.target.value);
function setClip(i) { const c = S.proj.audio[i]; S.clip = c; if (c) $('player').src = `data/${S.pid}/${c.file}`; }
$('clipSel').onchange = e => setClip(+e.target.value);

// ---------------------------------------------------------------- spectrogram view
const img = $('specImg'), ov = $('specOv');
function sizeCanvas() { const r = $('specWrap').getBoundingClientRect(); for (const c of [img, ov]) { c.width = Math.max(10, r.width | 0); c.height = Math.max(10, r.height | 0); } }
const X = t => (t - S.view.t0) / (S.view.t1 - S.view.t0) * ov.width;
const Y = f => (S.view.f1 - f) / (S.view.f1 - S.view.f0) * ov.height;
const T = x => S.view.t0 + x / ov.width * (S.view.t1 - S.view.t0);
const F = y => S.view.f1 - y / ov.height * (S.view.f1 - S.view.f0);
let raf = 0;
function drawSpec() {
  if (!S.proj) return; cancelAnimationFrame(raf);
  raf = requestAnimationFrame(() => { sizeCanvas(); drawSpecRegion(img.getContext('2d'), img.width, img.height, S.view, drawSpec); drawOverlay(); });
}
function curvePts(fn, t0, t1, n = 400) { const a = []; for (let i = 0; i <= n; i++) { const t = t0 + (t1 - t0) * i / n; a.push([t, fn(t)]); } return a; }
function polyline(ctx, pts, col, w = 1.5, dash = []) {
  ctx.strokeStyle = col; ctx.lineWidth = w; ctx.setLineDash(dash); ctx.beginPath(); let on = false;
  for (const [t, f] of pts) { if (f == null || !isFinite(f)) { on = false; continue; } const x = X(t), y = Y(f); on ? ctx.lineTo(x, y) : ctx.moveTo(x, y); on = true; }
  ctx.stroke(); ctx.setLineDash([]);
}
function shownEvents() {
  const ev = S.proj.events; if ($('showAll').checked || !S.usedIds.size) return ev;
  return ev.filter(e => S.usedIds.has(e.id) || e.selected);
}
function evColor(e) { const i = S.proj.events.indexOf(e); return S.usedIds.has(e.id) ? COLORS[i % COLORS.length] : '#888'; }
function drawOverlay() {
  if (!S.proj) return;
  const ctx = ov.getContext('2d'); ctx.clearRect(0, 0, ov.width, ov.height);
  const v = S.view; ctx.font = '11px sans-serif'; ctx.fillStyle = '#ccc';
  const span = v.t1 - v.t0, st = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800].find(s => span / s < 12) || 3600;
  for (let t = Math.ceil(v.t0 / st) * st; t <= v.t1; t += st) { ctx.fillRect(X(t), ov.height - 6, 1, 6); ctx.fillText(fmtT(t), X(t) + 2, ov.height - 8); }
  const fs = [5, 10, 20, 50, 100, 200, 500].find(s => (v.f1 - v.f0) / s < 10) || 1000;
  for (let f = Math.ceil(v.f0 / fs) * fs; f <= v.f1; f += fs) { ctx.fillRect(0, Y(f), 6, 1); ctx.fillText(f + ' Hz', 8, Y(f) + 4); }
  for (const e of shownEvents()) {
    const col = evColor(e);
    if ($('showLines').checked && e.line) { ctx.fillStyle = col; for (const [t, f] of e.line) if (t >= v.t0 && t <= v.t1) ctx.fillRect(X(t) - 1, Y(f) - 1, 2, 2); }
    if ($('showFits').checked && e.t0 > v.t0 - 90 && e.t0 < v.t1 + 90) polyline(ctx, curvePts(t => e.f0 * gDelayed(t, e.v, e.d, e.t0), e.t0 - 60, e.t0 + 60), col, S.usedIds.has(e.id) ? 1.6 : 1, [4, 3]);
    ctx.fillStyle = col; ctx.fillText(`#${e.id}`, X(e.t0) + 3, Y(e.f0 * 1.04));
  }
  if ($('showSlider').checked) {
    const q = sliders(), mode = document.querySelector('input[name=pmode]:checked').value;
    polyline(ctx, curvePts(t => q.f0 * (mode === 'delayed' ? gDelayed(t, q.v, q.d, q.t0) : gPosition(t, q.v, q.d, q.t0)), q.t0 - 90, q.t0 + 90), '#ffffff', 1.6);
  }
  const mc = S.res && S.res.curves[S.selRank];
  if (mc) mc.curves.forEach(c => polyline(ctx, c.t.map((t, i) => [t, c.f[i]]), '#76ff03', 1.4));
  if (S.clip && !$('player').paused || ($('player').currentTime > 0 && S.clip)) {
    const pt = S.clip.t0 + $('player').currentTime;
    if (pt >= v.t0 && pt <= v.t1) { ctx.fillStyle = '#ff0'; ctx.fillRect(X(pt), 0, 1, ov.height); }
  }
}
$('player').ontimeupdate = drawOverlay;
let drag = null;
ov.onmousedown = e => { const r = ov.getBoundingClientRect(); drag = {x: e.clientX - r.left, y: e.clientY - r.top, v: {...S.view}, moved: false}; };
window.onmouseup = e => {
  if (drag && !drag.moved && e.target === ov) {      // click = play from here (if a snippet covers it)
    const r = ov.getBoundingClientRect(), t = T(e.clientX - r.left);
    const i = S.proj.audio.findIndex(c => t >= c.t0 && t <= c.t1);
    if (i >= 0) { if (S.proj.audio[i] !== S.clip) { $('clipSel').value = i; setClip(i); } const p = $('player'); const go = () => { p.currentTime = t - S.clip.t0; p.play().catch(() => {}); }; p.readyState >= 1 ? go() : (p.onloadedmetadata = () => { p.onloadedmetadata = null; go(); }, p.load()); }
  }
  drag = null;
};
ov.onmousemove = e => {
  const r = ov.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  $('hover').textContent = `t ${fmtT(T(x))}  f ${F(y).toFixed(1)} Hz`;
  if (!drag) return;
  const dx = (x - drag.x) / ov.width * (drag.v.t1 - drag.v.t0), dy = (y - drag.y) / ov.height * (drag.v.f1 - drag.v.f0);
  if (Math.abs(x - drag.x) + Math.abs(y - drag.y) > 3) drag.moved = true;
  S.view = {t0: drag.v.t0 - dx, t1: drag.v.t1 - dx, f0: Math.max(0, drag.v.f0 + dy), f1: drag.v.f1 + dy}; drawSpec();
};
ov.onwheel = e => {
  e.preventDefault(); const r = ov.getBoundingClientRect(); const k = e.deltaY > 0 ? 1.25 : 0.8;
  if (e.shiftKey) { const f = F(e.clientY - r.top); S.view.f0 = Math.max(0, f - (f - S.view.f0) * k); S.view.f1 = f + (S.view.f1 - f) * k; }
  else { const t = T(e.clientX - r.left); S.view.t0 = t - (t - S.view.t0) * k; S.view.t1 = t + (S.view.t1 - t) * k; }
  drawSpec();
};
const zoomT = k => { const c = (S.view.t0 + S.view.t1) / 2, h = (S.view.t1 - S.view.t0) / 2 * k; S.view.t0 = c - h; S.view.t1 = c + h; drawSpec(); };
$('zoomIn').onclick = () => zoomT(0.5); $('zoomOut').onclick = () => zoomT(2);
$('zoomAll').onclick = () => { const s = S.proj.spec; S.view = {t0: s.t0, t1: s.t1, f0: s.f0, f1: Math.min(s.f1, 1000)}; drawSpec(); };
$('zoomDef').onclick = () => { const s = S.proj.spec; S.view = {t0: s.t0, t1: s.t1, f0: s.f0, f1: Math.min(s.f1, 1000)}; if (S.proj.meta.view) Object.assign(S.view, S.proj.meta.view); drawSpec(); };
for (const id of ['showLines', 'showFits', 'showSlider']) $(id).onchange = drawOverlay;
$('showAll').onchange = () => { renderEvents(); drawOverlay(); };
window.onresize = () => { drawSpec(); renderCompare(); };
function focusEvent(e) { S.view = {t0: e.t0 - 70, t1: e.t0 + 70, f0: e.f0 * 0.75, f1: e.f0 * 1.3}; drawSpec(); }

// ---------------------------------------------------------------- sliders (local playground)
const SL = ['f0', 'v', 'd', 't0'];
function sliders() { const o = {}; for (const k of SL) o[k] = parseFloat($('n_' + k).value); return o; }
function setSliders(q) { for (const k of SL) if (q[k] != null) { $('s_' + k).value = q[k]; $('n_' + k).value = (+q[k]).toFixed(k === 'd' ? 0 : 2); } updReadouts(); }
function updReadouts() { const q = sliders(), r = readouts(q.f0, q.v, q.d);
  $('readouts').textContent = `f_pre ${r.fpre.toFixed(2)} Hz · f_post ${r.fpost.toFixed(2)} Hz · max RoC ${r.roc.toFixed(3)} Hz/s · geometric CPA ${fmtT(q.t0 - q.d / C)}`; }
for (const k of SL) {
  $('s_' + k).oninput = () => { $('n_' + k).value = $('s_' + k).value; updReadouts(); drawOverlay(); };
  $('n_' + k).onchange = () => { $('s_' + k).value = $('n_' + k).value; updReadouts(); drawOverlay(); };
}
document.querySelectorAll('input[name=pmode]').forEach(r => r.onchange = drawOverlay);
$('fromView').onclick = () => { setSliders({t0: (S.view.t0 + S.view.t1) / 2}); drawOverlay(); };

// ---------------------------------------------------------------- events + gaps
function renderEvents() {
  const evs = shownEvents();
  $('evTable').innerHTML = `<tr><th>id</th><th>used</th><th>src</th><th>confidence</th><th>t0 (delayed CPA)</th><th>f0 Hz</th><th>v m/s [range]</th><th>d m [range]</th><th>rms Hz</th><th>pts</th><th></th></tr>` +
    evs.map(e => `<tr class="${S.usedIds.has(e.id) ? 'used' : ''}" data-id="${e.id}"><td style="color:${evColor(e)}">#${e.id}</td><td>${S.usedIds.has(e.id) ? '✓' : ''}</td>
    <td>${esc(e.source)}</td><td>${e.conf != null ? `<span class="conf" style="width:${Math.min(60, e.conf * 3)}px"></span> ${fx(e.conf, 1)}` : ''}</td>
    <td>${fmtT(e.t0)} ±${fx(e.t0_sigma || 0)}</td><td>${fx(e.f0)}</td><td>${fx(e.v, 1)} [${fx(e.v_min, 0)}–${fx(e.v_max, 0)}]</td>
    <td>${fx(e.d, 0)} [${fx(e.d_min, 0)}–${fx(e.d_max, 0)}]</td><td>${e.fit_rms_hz != null ? fx(e.fit_rms_hz) : ''}</td><td>${(e.line || []).length}</td>
    <td><button class="evgo">view</button></td></tr>`).join('');
  $('evTable').querySelectorAll('tr[data-id]').forEach(tr => {
    const e = S.proj.events.find(x => x.id === +tr.dataset.id);
    tr.querySelector('.evgo').onclick = () => { setSliders(e); focusEvent(e); };
  });
  // gaps between the events of the selected search (or the project's selected events)
  const used = (S.res ? S.res.events : S.proj.events.filter(e => e.selected)).slice().sort((a, b) => a.t0 - b.t0);
  const p = (S.res && S.res.params) || {offset_tol_s: 5, tol_sigma_k: 2};
  const rows = [];
  for (let j = 0; j < used.length; j++) for (let k = j + 1; k < used.length; k++)
    rows.push({a: used[j].id, b: used[k].id, gap: used[k].t0 - used[j].t0, tol: p.offset_tol_s + p.tol_sigma_k * Math.hypot(used[j].t0_sigma ?? 1, used[k].t0_sigma ?? 1)});
  $('gapTable').innerHTML = rows.length ? '<tr><th>pair</th><th>gap (s)</th><th>gap (m:s)</th><th>± tol (s)</th></tr>' +
    rows.map(r => `<tr><td>#${r.a} → #${r.b}</td><td>${r.gap.toFixed(2)}</td><td>${fmtT(r.gap)}</td><td>${r.tol.toFixed(2)}</td></tr>`).join('')
    : '<tr><td class="muted" style="text-align:left">single-flight search: one event, no gaps (the recording clock is used instead)</td></tr>';
}

// ---------------------------------------------------------------- results
async function showResult(rid, rank) {
  const meta = S.proj.results.find(x => x.id === rid); if (!meta) return;
  const my = ++SEQ, pid = S.pid;
  const j = await getJSON(`data/${pid}/${meta.file}`); if (my !== SEQ || pid !== S.pid) return;
  S.res = j; S.summary = j.summary || {}; S.compare = []; S.heat = null; S.usedIds = new Set(j.events.map(e => e.id));
  const s = S.summary, p = j.params || {};
  const opts = [p.include_jets ? 'jets included' : 'jets dropped', p.region ? `region ${p.region.map(x => (+x).toFixed(1)).join(', ')}` : 'whole day store',
    p.time_window ? `time window ${fmtUtc(p.time_window[0]).slice(11)}–${fmtUtc(p.time_window[1]).slice(11)}` : null, p.wind && p.wind !== 'off' ? `wind ${p.wind}/${p.wind_model}` : null].filter(Boolean).join(' · ');
  $('summary').innerHTML = s.status !== 'ok' ? `status: ${esc(s.status)}` :
    (j.mode === 'multi' ? `<b>multi-event</b> · ${j.events.length} events · gaps from fitted t0s ${(s.gaps || []).slice(1).map(g => (+g).toFixed(1)).join(', ')} s · candidates ${s.n_candidates} → offset ${s.n_offset} → silence ${s.n_silence1} → ${s.n_silence2} → dense ${s.n_dense}` :
      `<b>single-flight</b> · ${s.n} (point, flight) candidates · known start ${fmtUtc(j.rec_start)}`) +
    ` · flight-data day ${esc(j.day)} · ${fx(s.elapsed_s || 0, 0)} s compute<br>${esc(opts)}` +
    (s.truth ? `<br><b>ground-truth rank: ${s.truth_rank ?? 'not in top list'}</b>` + (s.combos && s.combos[0] ? ` · top match ${(s.combos[0].dist_to_truth_m / 1000).toFixed(2)} km from truth` : '') : '');
  $('jobLog').textContent = 'params: ' + JSON.stringify(p) + '\n\n' + (j.log || []).join('\n');
  renderEvents(); renderResults(); renderMap(); drawScatter();
  const n = (s.combos || []).length;
  if (n) await selectRank(rank && rank <= n ? rank : 1); else { drawSpec(); writeHash(); }
}
function renderResults() {
  const s = S.summary, N = S.res.mode === 'multi' ? S.res.events.length : 1;
  if (!s.combos) { $('resTable').innerHTML = ''; return; }
  $('resTable').innerHTML = `<tr><th>cmp</th><th>#</th><th>score</th>${[...Array(N).keys()].map(k => `<th>ev${k + 1} flight</th><th>ev${k + 1}</th>`).join('')}<th>dt s</th><th>lat, lon</th>${s.truth ? '<th>to truth</th>' : ''}</tr>` +
    s.combos.slice(0, 100).map(r => `<tr data-r="${r.rank}" class="${r.rank === S.selRank ? 'sel' : ''}"><td><input type="checkbox" class="cmpchk" ${S.compare.includes(r.rank) ? 'checked' : ''}></td><td>${r.rank}</td><td>${fx(r.total_score, 3)}</td>
      ${r.flights.map((f, k) => `<td title="hex ${esc(f.hex)} · ${esc(f.cls)}">${esc(f.reg || f.hex)} ${esc(f.type || '')}</td><td>${fx(r.event_scores[k] ?? 0)}</td>`).join('')}
      <td>${r.dt_s != null ? fx(r.dt_s) : ''}</td><td>${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}</td>${s.truth ? `<td>${(r.dist_to_truth_m / 1000).toFixed(2)} km</td>` : ''}</tr>`).join('');
  $('resTable').querySelectorAll('tr[data-r]').forEach(tr => {
    const rank = +tr.dataset.r;
    tr.onclick = e => { if (e.target.classList.contains('cmpchk')) return; selectRank(rank); };
    tr.querySelector('.cmpchk').onchange = e => { if (e.target.checked) { S.compare.push(rank); if (S.compare.length > 3) S.compare.shift(); } else S.compare = S.compare.filter(x => x !== rank); renderResults(); renderCompare(); };
  });
}
async function selectRank(rank) {
  S.selRank = rank; renderResults();
  const r = S.summary.combos[rank - 1]; map.setView([r.lat, r.lon], Math.max(map.getZoom(), 12)); selMarker.setLatLng([r.lat, r.lon]).addTo(map);
  if (!S.compare.includes(rank) && S.compare.length < 3) S.compare.push(rank);
  const mc = S.res.curves[rank];
  if (mc && S.res.events[0]) { const e = S.res.events[0]; S.view = {t0: e.t0 - 80, t1: e.t0 + 80, f0: e.f0 * 0.7, f1: e.f0 * 1.35}; if (S.res.events.length > 1) { const a = S.res.events[0].t0, b = S.res.events[S.res.events.length - 1].t0; S.view.t0 = a - 120; S.view.t1 = b + 120; S.view.f0 = Math.min(...S.res.events.map(e => e.f0)) * 0.7; S.view.f1 = Math.max(...S.res.events.map(e => e.f0)) * 1.35; } }
  { const sp = S.proj.spec; S.view.t0 = Math.max(S.view.t0, sp.t0); S.view.t1 = Math.min(S.view.t1, sp.t1); }
  drawSpec(); renderCompare();
  $('selLinks').innerHTML = `#${rank} ${r.lat.toFixed(6)}, ${r.lon.toFixed(6)} · ` + gmaps(r.lat, r.lon) + (mc ? '' : ' · <span class="muted">predicted curves were only cached for the top ranks</span>');
  S.heat = S.res.heat[rank] || null;
  if (S.heat) drawHeat(); else { if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; } $('heatMsg').textContent = 'no precomputed heat map for this rank (heat maps were exported for the top 1–3)'; }
  writeHash();
}

// ---------------------------------------------------------------- map
const map = L.map('map').setView([39.5, -98], 4);
const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© OpenStreetMap contributors'}).addTo(map);
const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {maxZoom: 19, attribution: 'Imagery © Esri'});
L.control.layers({'Map': osm, 'Satellite': sat}).addTo(map); L.control.scale().addTo(map);
const gmaps = (la, lo) => `<a target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${la},${lo}">Google Maps</a> · <a target="_blank" rel="noopener" href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${la},${lo}">Street View</a>`;
let ptLayer = L.layerGroup().addTo(map), heatLayer = null, truthMarker = null;
const selMarker = L.circleMarker([0, 0], {radius: 9, color: '#76ff03', weight: 3, fill: false});
function clearMap() { ptLayer.clearLayers(); if (heatLayer) map.removeLayer(heatLayer); heatLayer = null; if (truthMarker) map.removeLayer(truthMarker); truthMarker = null; map.removeLayer(selMarker); }
function scoreColor(x) { const h = 240 - 240 * Math.max(0, Math.min(1, x)); return `hsl(${h},90%,55%)`; }
function renderMap() {
  clearMap(); const s = S.summary, truth = s.truth || (S.proj.meta.truth ? {lat: S.proj.meta.truth[0], lon: S.proj.meta.truth[1]} : null);
  if (truth) truthMarker = L.marker([truth.lat, truth.lon], {title: 'ground truth'}).bindPopup('ground truth' + (S.proj.meta.status === 'unsolved' ? ' (geotag)' : '')).addTo(map);
  const lay = S.res.mode === 'multi' ? ['combined', ...S.res.events.map((_, k) => 'event' + (k + 1))] : ['event1'];
  $('heatLayer').innerHTML = lay.map(l => `<option>${l}</option>`).join('');
  if (!s.combos || !s.combos.length) return;
  const top = s.combos.slice(0, 200), lo = top[top.length - 1].total_score, hi = top[0].total_score;
  if ($('showPts').checked) top.slice().reverse().forEach(r => {
    L.circleMarker([r.lat, r.lon], {radius: r.rank <= 10 ? 7 : 4, color: '#000', weight: 1, fillColor: scoreColor((r.total_score - lo) / (hi - lo + 1e-9)), fillOpacity: 0.9})
      .bindTooltip(`#${r.rank} ${fx(r.total_score, 3)} · ${r.flights.map(f => esc(f.reg || f.hex)).join(' + ')}`).on('click', () => selectRank(r.rank)).addTo(ptLayer);
  });
  if (truth) map.setView([truth.lat, truth.lon], 11); else map.setView([top[0].lat, top[0].lon], 9);
}
$('showPts').onchange = () => { renderMap(); if (S.summary.combos) { selMarker.addTo(map); if (S.heat) drawHeat(); } };
$('heatLayer').onchange = drawHeat;
function drawHeat() {
  if (!S.heat) return; const v = S.heat.layers[$('heatLayer').value] || S.heat.layers[Object.keys(S.heat.layers)[0]]; if (!v) return;
  const ok = v.filter(x => x != null && x > -1e8).sort((a, b) => a - b); if (!ok.length) return;
  const mx = ok[ok.length - 1], mn = ok[Math.floor(ok.length * 0.5)];
  const pts = []; for (let i = 0; i < v.length; i++) if (v[i] != null && v[i] > mn) pts.push([S.heat.lat[i], S.heat.lon[i], ((v[i] - mn) / (mx - mn + 1e-12)) ** 2]);
  if (heatLayer) map.removeLayer(heatLayer);
  heatLayer = L.heatLayer(pts, {radius: 14, blur: 12, max: 1, minOpacity: 0.2}).addTo(map);
  let bi = -1; for (let i = 0; i < v.length; i++) if (v[i] != null && (bi < 0 || v[i] > v[bi])) bi = i;
  const t = S.summary.truth;
  $('heatMsg').textContent = `heat-map peak ${S.heat.lat[bi].toFixed(6)}, ${S.heat.lon[bi].toFixed(6)}` + (t ? ` · ${haversine(S.heat.lat[bi], S.heat.lon[bi], t.lat, t.lon).toFixed(0)} m from truth` : '');
}

// ---------------------------------------------------------------- scatter / histogram
function drawScatter() {
  const cv = $('scatter'), ctx = cv.getContext('2d'); ctx.clearRect(0, 0, cv.width, cv.height); ctx.fillStyle = '#111'; ctx.fillRect(0, 0, cv.width, cv.height);
  const s = S.summary; ctx.fillStyle = '#aaa'; ctx.font = '11px sans-serif';
  if (s.histogram) {
    const h = s.histogram, mx = Math.max(...h.counts), n = h.counts.length;
    h.counts.forEach((c, i) => { ctx.fillStyle = '#4fc3f7'; const w = (cv.width - 40) / n; ctx.fillRect(30 + i * w, cv.height - 20 - c / mx * (cv.height - 40), w - 1, c / mx * (cv.height - 40)); });
    ctx.fillStyle = '#aaa'; ctx.fillText(`score histogram (−mean |f_model − f_line| Hz): ${fx(h.edges[0], 1)} … ${fx(h.edges[n])}`, 30, 12);
    if (s.combos && s.combos[0]) { const b = s.combos[0].total_score; const x = 30 + (b - h.edges[0]) / (h.edges[n] - h.edges[0]) * (cv.width - 40); ctx.fillStyle = '#76ff03'; ctx.fillRect(x, 16, 2, cv.height - 36); ctx.fillText('best', x + 4, 26); }
    return;
  }
  const p = s.points; if (!p || !p.ev || p.ev.length < 2) { ctx.fillText('no scatter for this result', 10, 20); return; }
  const a = p.ev[0], b = p.ev[1]; let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
  for (const x of a) { if (x < a0) a0 = x; if (x > a1) a1 = x; } for (const x of b) { if (x < b0) b0 = x; if (x > b1) b1 = x; }
  const px = x => 35 + (x - a0) / (a1 - a0 + 1e-9) * (cv.width - 45), py = y => cv.height - 20 - (y - b0) / (b1 - b0 + 1e-9) * (cv.height - 35);
  for (let i = p.lat.length - 1; i >= 0; i--) { const c = p.combo[i]; ctx.fillStyle = c >= 0 && c < 10 ? COLORS[c % COLORS.length] : '#555'; ctx.fillRect(px(a[i]), py(b[i]), 2, 2); }
  ctx.fillStyle = '#aaa'; ctx.fillText('event 1 score →', cv.width - 110, cv.height - 5); ctx.fillText('↑ event 2 score  (colour = top-10 combos)', 38, 12);
}

// ---------------------------------------------------------------- side-by-side comparison
function renderCompare() {
  const box = $('compare'); box.innerHTML = '';
  if (!S.res || !S.summary.combos) return;
  for (const rank of S.compare) {
    const r = S.summary.combos[rank - 1], mc = S.res.curves[rank];
    const div = document.createElement('div'); div.className = 'cmp';
    const holder = document.createElement('div'); div.appendChild(holder);
    const info = document.createElement('div'); info.className = 'small';
    info.innerHTML = `<b>#${rank}</b> score ${fx(r.total_score, 3)} · ${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}${r.dist_to_truth_m != null ? ` · ${(r.dist_to_truth_m / 1000).toFixed(2)} km to truth` : ''} · ${gmaps(r.lat, r.lon)}<br>` +
      r.flights.map((f, k) => `ev${k + 1}: <b>${esc(f.reg || '?')}</b> ${esc(f.type || '')} (hex ${esc(f.hex)}) · CPA ${fx(r.cpa_distance_m[k] / 1000)} km @ ${fmtUtc(r.cpa_time_utc[k]).slice(11)} · f0 ${fx(r.f0[k])} · s ${fx(r.event_scores[k] ?? 0, 3)}`).join('<br>') +
      (mc ? '' : '<br><span class="muted">predicted curves not exported for this rank</span>');
    div.appendChild(info); box.appendChild(div);
    const evs = S.res.mode === 'multi' ? S.res.events : [S.res.events[0]];
    evs.forEach((e, k) => {
      const cv = document.createElement('canvas'); holder.appendChild(cv); cv.width = 420; cv.height = 170;
      const v = {t0: e.t0 - 60, t1: e.t0 + 60, f0: e.f0 * 0.8, f1: e.f0 * 1.25};
      const draw = () => {
        if (!cv.isConnected) return; const c = cv.getContext('2d');
        drawSpecRegion(c, cv.width, cv.height, v, draw);
        const x = t => (t - v.t0) / (v.t1 - v.t0) * cv.width, y = f => (v.f1 - f) / (v.f1 - v.f0) * cv.height;
        if (e.line) { c.fillStyle = '#4fc3f7'; for (const [t, f] of e.line) c.fillRect(x(t) - 1, y(f) - 1, 2, 2); }
        if (mc && mc.curves[k]) { const cc = mc.curves[k]; c.strokeStyle = '#76ff03'; c.lineWidth = 1.5; c.beginPath(); let on = false; cc.t.forEach((t, i) => { const f = cc.f[i]; if (f == null) { on = false; return; } on ? c.lineTo(x(t), y(f)) : c.moveTo(x(t), y(f)); on = true; }); c.stroke(); }
        c.fillStyle = '#fff'; c.font = '11px sans-serif'; c.fillText(`event ${k + 1} (#${e.id}): predicted (green) vs traced (blue)`, 4, 12);
      };
      draw();
    });
  }
}

// ---------------------------------------------------------------- about
function showAbout(on) { $('about').hidden = !on; if (on) $('about').scrollIntoView({behavior: 'smooth'}); }
$('aboutBtn').onclick = () => showAbout($('about').hidden); $('aboutClose').onclick = () => showAbout(false);
$('aboutLink').onclick = e => { e.preventDefault(); showAbout(true); };

init().catch(e => { console.error(e); document.body.insertAdjacentHTML('afterbegin', `<div id="statusBanner">Failed to load data: ${esc(e.message)}</div>`); });
