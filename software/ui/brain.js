/* The eye, driven by the real cascade.
 *
 * Every node and edge drawn here came out of an actual spreading-activation
 * run: the same `Cascade` object that `you2 why` prints. Nothing is
 * simulated, and nothing is decorative — the layout IS the explanation.
 *
 *   Cue seeds ignite on the rim.
 *   Each hop moves one ring inward, along the edge it actually travelled.
 *   Workspace winners arrive at the centre and are named.
 *   Neurons that lateral inhibition suppressed visibly collapse.
 *
 * On a round 800x800 panel that reads as thought converging, which is what
 * it is. Canvas 2D only: a Pi 5 has no business running WebGL for this, and
 * the whole thing has to stay well under one core so it never competes with
 * ASR.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const eye = $('eye'), cv = $('stage'), ctx = cv.getContext('2d', { alpha: false });
const R = 400, CX = 400, CY = 400;   // the panel is 800x800; the eye is inscribed

const S = {
  state: 'boot',
  nodes: new Map(),      // id -> {x,y,tx,ty,r,hue,a,lit,winner,suppressed,label,hop}
  edges: [],             // {a,b,t,fire}
  order: [],             // firing order, replayed as an animation
  cursor: 0,             // how far through `order` we are
  t0: 0,
  level: 0,              // mic/output level for the waveform
  levelTarget: 0,
  slow: false,
  constellation: null,
  pulses: [],            // travelling activation packets
  lastFrame: 0,
};

/* ------------------------------------------------------------------ layout */

/* Concentric rings by hop depth. Deterministic angles from the id, so a
 * neuron sits in the same place every time it fires — which is what makes the
 * picture legible across turns rather than a fresh scramble each time. */
function place(node, index, total) {
  const hop = Math.min(node.hop ?? 3, 4);
  const ring = node.winner ? 0 : hop;
  // The centre is reserved for what won — as text, because a person needs to
  // read the answer, not admire the graph. So the winners converge to a tight
  // ring just outside the text and the rest of the cascade sits beyond them.
  // The picture is activation collapsing onto an answer rather than onto a
  // dot, which is also the more honest description of what happened.
  const radius = [244, 292, 322, 350, 368][ring] ?? 368;
  // Golden-angle spread, offset by id, so nodes in the same ring never stack.
  const golden = 2.399963229728653;
  const angle = (node.id * golden) + (index / Math.max(1, total)) * 0.6;
  const jitter = ring === 0 ? 0 : 12 * Math.sin(node.id * 1.7);
  return {
    x: CX + Math.cos(angle) * (radius + jitter),
    y: CY + Math.sin(angle) * (radius + jitter),
  };
}

function loadCascade(scene) {
  S.nodes.clear();
  S.edges = [];
  S.pulses = [];
  const total = scene.nodes.length;
  scene.nodes.forEach((n, i) => {
    const p = place(n, i, total);
    S.nodes.set(n.id, {
      ...n,
      // Everything starts at the rim and travels in: activation arriving.
      x: CX + (p.x - CX) * 1.9, y: CY + (p.y - CY) * 1.9,
      tx: p.x, ty: p.y,
      r: n.winner ? 6.5 : 2.5 + 4.5 * (n.a ?? 0),
      lit: 0,
    });
  });
  S.edges = scene.edges.filter(e => S.nodes.has(e.from) && S.nodes.has(e.to))
    .map(e => ({ a: e.from, b: e.to, t: e.type, fire: 0 }));
  S.order = scene.order.filter(id => S.nodes.has(id));
  S.cursor = 0;
  S.t0 = performance.now();
}

/* ------------------------------------------------------------------ drawing */

function ringGlow() {
  const g = ctx.createRadialGradient(CX, CY, 40, CX, CY, R);
  const accent = getComputedStyle(eye).getPropertyValue('--accent').trim() || '#6f8cff';
  g.addColorStop(0, hexA(accent, 0.10));
  g.addColorStop(0.55, hexA(accent, 0.03));
  g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 800, 800);
}

function hexA(hex, a) {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function drawCascade(now) {
  // Replay the firing order in real time. Slow motion exists because the real
  // cascade finishes in about two milliseconds, and the whole point of this
  // screen is that a person can watch it happen.
  const step = S.slow ? 260 : 42;
  const want = Math.floor((now - S.t0) / step);
  while (S.cursor < want && S.cursor < S.order.length) {
    const id = S.order[S.cursor++];
    const n = S.nodes.get(id);
    if (n) {
      n.lit = 1;
      for (const e of S.edges) {
        if (e.a === id && S.nodes.has(e.b)) {
          e.fire = 1;
          S.pulses.push({ a: e.a, b: e.b, p: 0, hue: n.hue });
        }
      }
    }
  }

  // Edges first, underneath everything.
  for (const e of S.edges) {
    const a = S.nodes.get(e.a), b = S.nodes.get(e.b);
    if (!a || !b) continue;
    const strength = e.fire ? 0.42 : 0.08;
    ctx.strokeStyle = `hsla(${a.hue} 60% 62% / ${strength * (0.35 + 0.65 * a.lit)})`;
    ctx.lineWidth = e.fire ? 1.5 : 0.7;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    e.fire *= 0.985;
  }

  // Activation packets travelling along the edge they actually took.
  for (const pulse of S.pulses) {
    const a = S.nodes.get(pulse.a), b = S.nodes.get(pulse.b);
    if (!a || !b) { pulse.p = 2; continue; }
    pulse.p += S.slow ? 0.014 : 0.05;
    const x = a.x + (b.x - a.x) * pulse.p, y = a.y + (b.y - a.y) * pulse.p;
    ctx.fillStyle = `hsla(${pulse.hue} 90% 72% / ${Math.max(0, 1 - pulse.p)})`;
    ctx.beginPath(); ctx.arc(x, y, 2.6, 0, 7); ctx.fill();
  }
  S.pulses = S.pulses.filter(p => p.p < 1);

  // Neurons.
  for (const n of S.nodes.values()) {
    n.x += (n.tx - n.x) * 0.11;
    n.y += (n.ty - n.y) * 0.11;
    // Lateral inhibition, made visible: a suppressed neuron collapses.
    const target = n.suppressed ? 0.16 : 1;
    n.lit += (n.lit * target - n.lit) * 0.06;

    const glow = n.lit * (n.winner ? 1 : 0.72);
    if (glow > 0.02) {
      const g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 5);
      g.addColorStop(0, `hsla(${n.hue} 90% 70% / ${0.5 * glow})`);
      g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r * 5, 0, 7); ctx.fill();
    }
    ctx.fillStyle = `hsla(${n.hue} ${n.suppressed ? 20 : 85}% ${52 + 26 * glow}% / ${0.35 + 0.65 * glow})`;
    ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 7); ctx.fill();

    if (n.winner && glow > 0.5) {
      ctx.strokeStyle = `hsla(${n.hue} 90% 78% / ${0.5 * glow})`;
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 5 + 3 * Math.sin(now / 320), 0, 7);
      ctx.stroke();
    }
    if (n.pinned) {
      ctx.strokeStyle = `hsla(${n.hue} 90% 80% / .55)`;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r + 3, 0, 7); ctx.stroke();
    }
  }
}

function drawWaveform(now, hue) {
  // Radial waveform on live level. Amplitude is real, so a quiet room reads
  // as a quiet ring rather than as a screensaver.
  const bars = 96;
  S.level += (S.levelTarget - S.level) * 0.16;
  for (let i = 0; i < bars; i++) {
    const angle = (i / bars) * Math.PI * 2;
    const noise = 0.5 + 0.5 * Math.sin(now / 220 + i * 0.7) * Math.sin(now / 90 + i);
    const amp = 14 + S.level * 96 * noise;
    const r0 = 262, r1 = r0 + amp;
    ctx.strokeStyle = `hsla(${hue} 80% 62% / ${0.25 + 0.6 * S.level})`;
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(CX + Math.cos(angle) * r0, CY + Math.sin(angle) * r0);
    ctx.lineTo(CX + Math.cos(angle) * r1, CY + Math.sin(angle) * r1);
    ctx.stroke();
  }
}

function drawThinking(now) {
  // One slow arc. Deliberately the least interesting animation in the set:
  // waiting is not an achievement.
  ctx.strokeStyle = 'hsla(38 95% 62% / .8)';
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(CX, CY, 250, now / 520, now / 520 + 1.15);
  ctx.stroke();
}

function drawConstellation(now) {
  const c = S.constellation;
  if (!c) return;
  const breathe = 0.72 + 0.28 * Math.sin(now / 2600);
  for (const e of c.edges) {
    const a = c.pos.get(e.from), b = c.pos.get(e.to);
    if (!a || !b) continue;
    ctx.strokeStyle = `rgba(120,140,190,${0.06 * breathe * (e.w ?? .4)})`;
    ctx.lineWidth = 0.6;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }
  for (const n of c.nodes) {
    const p = c.pos.get(n.id);
    if (!p) continue;
    const tw = 0.5 + 0.5 * Math.sin(now / 1400 + n.id);
    ctx.fillStyle = `hsla(${n.hue} 55% 62% / ${(0.10 + 0.16 * tw) * breathe})`;
    ctx.beginPath(); ctx.arc(p.x, p.y, n.pinned ? 2.4 : 1.6, 0, 7); ctx.fill();
  }
}

function drawDream(now) {
  // Replay: packets sweeping the ring, and the ring tightening as downscaling
  // prunes. Slow, quiet, and obviously not awake.
  for (let i = 0; i < 5; i++) {
    const phase = (now / 4200 + i / 5) % 1;
    const r = 120 + phase * 220;
    ctx.strokeStyle = `hsla(265 70% 66% / ${0.22 * (1 - phase)})`;
    ctx.lineWidth = 1.6;
    ctx.beginPath(); ctx.arc(CX, CY, r, 0, 7); ctx.stroke();
  }
  for (let i = 0; i < 40; i++) {
    const a = (i / 40) * Math.PI * 2 + now / 9000;
    const r = 200 + 70 * Math.sin(now / 1700 + i);
    ctx.fillStyle = `hsla(${265 + 40 * Math.sin(i)} 70% 70% / .32)`;
    ctx.beginPath(); ctx.arc(CX + Math.cos(a) * r, CY + Math.sin(a) * r, 1.8, 0, 7);
    ctx.fill();
  }
}

function frame(now) {
  ctx.fillStyle = '#06070a';
  ctx.fillRect(0, 0, 800, 800);
  ringGlow();

  switch (S.state) {
    case 'remembering': drawCascade(now); break;
    case 'listening':   drawWaveform(now, 162); break;
    case 'speaking':    drawWaveform(now, 26); break;
    case 'thinking':    drawThinking(now); break;
    case 'dreaming':    drawDream(now); break;
    default:            drawConstellation(now);
  }

  if (S.state === 'remembering') {
    // Darken the middle so the answer stays readable over the cascade.
    const v = ctx.createRadialGradient(CX, CY, 60, CX, CY, 240);
    v.addColorStop(0, 'rgba(6,7,10,0.90)');
    v.addColorStop(1, 'rgba(6,7,10,0)');
    ctx.fillStyle = v;
    ctx.beginPath(); ctx.arc(CX, CY, 240, 0, 7); ctx.fill();
  }

  // The rim: a thin ring in the state colour, readable from across a room
  // even when the middle of the screen is dark.
  const accent = getComputedStyle(eye).getPropertyValue('--accent').trim() || '#6f8cff';
  ctx.strokeStyle = hexA(accent, 0.5);
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(CX, CY, R - 3, 0, 7); ctx.stroke();

  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* -------------------------------------------------------------------- state */

function fit() {
  // Reserve room for the bench when there is any, so the eye is never
  // underneath it.
  const benchWidth = getComputedStyle($('bench')).display === 'none' ? 0 : 400;
  const scale = Math.min(1, (window.innerWidth - benchWidth - 24) / 800,
                         (window.innerHeight - 24) / 800);
  document.documentElement.style.setProperty('--scale', Math.max(0.2, scale));
}
window.addEventListener('resize', fit);
fit();

/* Minimum time a state stays on screen, in ms.
 *
 * The brain finishes a retrieval in about twelve milliseconds, so without
 * this the most interesting thing the device does is never seen: the cascade
 * is replaced by the reply before a single frame of it renders.
 *
 * This is a *display* dwell and nothing else — the pipeline is never slowed
 * down for a screen. On real hardware there is 600-800 ms of ASR, network and
 * TTS latency to fill anyway, so the animation is playing during time that is
 * already being spent. Off-device it just holds a moment longer. */
const DWELL = { remembering: 1100, thinking: 350, speaking: 900, dreaming: 600 };

let stateQueue = [], stateFreeAt = 0, statePump = null;

function setState(s) {
  stateQueue.push(s);
  pumpState();
}

function pumpState() {
  if (statePump || !stateQueue.length) return;
  const wait = Math.max(0, stateFreeAt - performance.now());
  statePump = setTimeout(() => {
    statePump = null;
    const next = stateQueue.shift();
    if (next === undefined) return;
    // Collapse a run of identical states rather than queueing a dwell each.
    while (stateQueue[0] === next) stateQueue.shift();
    applyState(next);
    stateFreeAt = performance.now() + (DWELL[next] || 0);
    pumpState();
  }, wait);
}

function applyState(s) {
  S.state = s;
  eye.dataset.state = s;
  $('statename').textContent = s;
  $('benchstate').textContent = s;
  if (s === 'remembering') S.t0 = performance.now();   // replay from the start
}

function kindShort(k) {
  return ({ episode: 'said', fact: 'known', entity: 'who', schema: 'pattern',
            insight: 'noticed', loop: 'open', affect: 'felt' })[k] || k;
}

function showWorkspace(scene) {
  $('rquery').textContent = scene.query ? `“${scene.query}”` : '';
  const ul = $('workspace');
  ul.innerHTML = '';
  const byId = new Map(scene.nodes.map(n => [n.id, n]));
  scene.workspace.slice(0, 7).forEach((id, i) => {
    const n = byId.get(id);
    if (!n) return;
    const li = document.createElement('li');
    li.style.animationDelay = `${(S.slow ? 260 : 90) * i}ms`;
    li.style.setProperty('--h', n.hue);
    if (n.derived) li.classList.add('derived');
    li.innerHTML = `<span class="k">${kindShort(n.kind)}</span><span class="v"></span>`;
    li.querySelector('.v').textContent = n.label;
    ul.appendChild(li);
  });
  for (const inf of (scene.inferences || []).slice(0, 2)) {
    const li = document.createElement('li');
    li.className = 'derived';
    li.style.setProperty('--h', 150);
    li.innerHTML = `<span class="k">inferred</span><span class="v"></span>`;
    li.querySelector('.v').textContent =
      `${inf.claim} (${Math.round(inf.confidence * 100)}%)`;
    ul.appendChild(li);
  }
  $('rmeta').textContent =
    `${scene.visited} neurons visited · ${scene.workspace.length} in the workspace · ` +
    `${scene.tokens} tokens${scene.exhausted ? '' : ' · budget hit'}`;
}

/* ------------------------------------------------------------------ events */

const es = new EventSource('/events');

es.addEventListener('state', (e) => setState(JSON.parse(e.data).state));

es.addEventListener('cascade', (e) => {
  const scene = JSON.parse(e.data);
  loadCascade(scene);
  showWorkspace(scene);
  // The cascade frame and the state change race over SSE; align the replay
  // with whenever `remembering` actually lands on screen.
  S.pendingCascade = true;
  log(`cascade · ${scene.visited} visited · ${scene.workspace.length} won`, 'ac');
});

es.addEventListener('transcript', (e) => {
  const d = JSON.parse(e.data);
  $('transcript').textContent = d.text || '';
});

es.addEventListener('level', (e) => { S.levelTarget = JSON.parse(e.data).level || 0; });

es.addEventListener('reply', (e) => {
  const d = JSON.parse(e.data);
  $('transcript').textContent = d.text || '';
  log(`${d.route} · ${Math.round(d.ms)}ms · ${d.tokens}tok`, 'hi');
});

es.addEventListener('night', (e) => {
  const d = JSON.parse(e.data);
  $('dstage').textContent = d.stage || 'consolidating';
  $('dstats').innerHTML = [
    ['episodes replayed', d.episodes], ['facts learned', d.facts_new],
    ['facts replaced', d.facts_reversed], ['synapses pruned', d.pruned],
    ['inferences compiled', d.compiled], ['insights kept', d.insights],
  ].map(([k, v]) => `<li>${k}<b>${v ?? 0}</b></li>`).join('');
  if (d.journal) log(d.journal);
});

es.addEventListener('ping', () => {});
es.onerror = () => log('connection lost — retrying', 'ac');

function log(text, cls) {
  const el = $('log');
  const line = document.createElement('div');
  if (cls) line.className = cls;
  line.textContent = text;
  el.appendChild(line);
  while (el.childElementCount > 200) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

/* -------------------------------------------------------------------- idle */

function tickClock() {
  const now = new Date();
  $('clock').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  $('date').textContent = now.toLocaleDateString([], {
    weekday: 'long', day: 'numeric', month: 'long' }).toLowerCase();
}
tickClock();
setInterval(tickClock, 10_000);

async function loadConstellation() {
  try {
    const c = await (await fetch('/api/constellation')).json();
    // Deterministic golden-angle spiral: the same store always draws the same
    // portrait, so a familiar shape means a familiar mind.
    const pos = new Map();
    c.nodes.forEach((n, i) => {
      const t = i / Math.max(1, c.nodes.length);
      const r = 70 + Math.sqrt(t) * 290;
      const a = i * 2.399963229728653;
      pos.set(n.id, { x: CX + Math.cos(a) * r, y: CY + Math.sin(a) * r });
    });
    S.constellation = { ...c, pos };
    $('held').textContent =
      `${c.neurons} neurons · ${c.synapses} synapses · ${c.facts} things known`;
    $('benchcount').textContent = `${c.neurons} neurons`;
  } catch { /* the brain may not be up yet; the clock still works */ }
}
loadConstellation();
setInterval(loadConstellation, 60_000);

/* ------------------------------------------------------------------- bench */

$('askform').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = $('ask');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  log(`> ${text}`, 'hi');
  try {
    await fetch('/api/say', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  } catch { log('could not reach the brain'); }
});

$('btn-night').addEventListener('click', async () => {
  log('running the night…', 'ac');
  await fetch('/api/night', { method: 'POST' });
  loadConstellation();
});

$('btn-idle').addEventListener('click', () => { applyState('idle');
fit(); loadConstellation(); });
$('slow').addEventListener('change', (e) => { S.slow = e.target.checked; });

applyState('idle');
fit();
