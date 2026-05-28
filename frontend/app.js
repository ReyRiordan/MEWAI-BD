// ── State ──────────────────────────────────────────────
let scenario = null;
let actionsTaken = new Set();   // action_type strings detected this run
let badgeTimer = null;
let ws = null;
let gameOver = false;
let layerElements = {};  // action_type (or "__patient__") -> <img> element

// ── WebSocket ──────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.addEventListener('message', e => handleEvent(JSON.parse(e.data)));
  ws.addEventListener('close', () => {
    setTimeout(connectWS, 1000);
  });
}

function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

// ── Event handler ──────────────────────────────────────
function handleEvent(evt) {
  switch (evt.type) {
    case 'state_update':      onStateUpdate(evt);    break;
    case 'action_detected':   onActionDetected(evt); break;
    case 'timer':             onTimer(evt);           break;
    case 'game_over':         onGameOver(evt);        break;
    case 'transcript_update': onTranscript(evt);     break;
  }
}

// ── Layer compositing ──────────────────────────────────
function initLayers(scen) {
  const container = document.getElementById('scene-layers');
  container.innerHTML = '';
  layerElements = {};

  // Build entries: actions with non-null layer + special patient layer
  const entries = [];
  scen.actions.forEach(action => {
    if (action.layer !== null && action.layer !== undefined) {
      entries.push({ key: action.type, layer: action.layer });
    }
  });
  entries.push({ key: '__patient__', layer: 0 });

  // Sort ascending by layer value; assign z-index by sort order
  entries.sort((a, b) => a.layer - b.layer);
  entries.forEach((entry, idx) => {
    const img = document.createElement('img');
    img.className = 'scene-layer';
    img.style.zIndex = String(idx + 1);
    img.style.opacity = '0';
    container.appendChild(img);
    layerElements[entry.key] = img;
  });
}

function setLayerSrc(img, newSrc) {
  if (img.getAttribute('data-src') === newSrc) {
    img.style.opacity = '1';
    return;
  }
  img.setAttribute('data-src', newSrc);
  img.style.opacity = '0';
  setTimeout(() => { img.src = newSrc; img.style.opacity = '1'; }, 200);
}

function renderLayers(escalation, activeActions) {
  if (!scenario) return;
  const activeSet = new Set(activeActions);

  // Patient layer
  const patientImg = layerElements['__patient__'];
  if (patientImg) {
    setLayerSrc(patientImg, `/visuals/patient_${escalation}.png`);
  }

  // Action layers
  scenario.actions.forEach(action => {
    if (action.layer === null || action.layer === undefined) return;
    const img = layerElements[action.type];
    if (!img) return;
    const isActive = activeSet.has(action.type);
    const visual = isActive ? action.active : action.inactive;
    if (!visual) {
      img.style.opacity = '0';
      return;
    }
    setLayerSrc(img, `/visuals/${visual}`);
  });
}

// ── state_update ───────────────────────────────────────
function onStateUpdate(evt) {
  renderLayers(evt.escalation, evt.active_actions || []);
  updateEscBar(evt.escalation, evt.max);
}

function updateEscBar(esc, max) {
  const pct = max > 0 ? (esc / max) * 100 : 0;
  const fill = document.getElementById('esc-fill');
  const label = document.getElementById('esc-value');
  fill.style.width = `${pct}%`;
  label.textContent = `${esc} / ${max}`;
  if (pct < 30)       fill.style.backgroundColor = '#22c55e';
  else if (pct < 60)  fill.style.backgroundColor = '#eab308';
  else if (pct < 80)  fill.style.backgroundColor = '#f97316';
  else                fill.style.backgroundColor = '#ef4444';
}

// ── action_detected ────────────────────────────────────
function onActionDetected(evt) {
  actionsTaken.add(evt.action_type);

  const badge = document.getElementById('action-badge');
  badge.className = 'action-badge';
  badge.classList.add(evt.point_change < 0 ? 'good' : 'bad');
  badge.textContent = `${evt.action_type}: ${evt.desc}`;
  badge.style.display = 'block';
  if (badgeTimer) clearTimeout(badgeTimer);
  badgeTimer = setTimeout(() => { badge.style.display = 'none'; }, 3000);
}

// ── timer ──────────────────────────────────────────────
function onTimer(evt) {
  const remaining = Math.max(0, evt.limit - evt.elapsed);
  const mins = Math.floor(remaining / 60);
  const secs = remaining % 60;
  const timerEl = document.getElementById('timer');
  timerEl.textContent = `${mins}:${String(secs).padStart(2, '0')}`;
  timerEl.classList.toggle('urgent', remaining < 30);
}

// ── game_over ──────────────────────────────────────────
function onGameOver(evt) {
  if (gameOver) return;
  gameOver = true;
  setTimeout(() => showEndScreen(evt.status, evt.reason), 600);
}

// ── transcript ─────────────────────────────────────────
function onTranscript(evt) {
  const panel = document.getElementById('transcript');
  const waiting = document.getElementById('waiting');

  if (evt.role === 'student') {
    waiting.classList.add('visible');
  } else {
    waiting.classList.remove('visible');
  }

  const div = document.createElement('div');
  div.className = `msg ${evt.role}`;
  const label = document.createElement('div');
  label.className = 'msg-label';
  label.textContent = evt.role === 'student' ? 'You' : 'Patient';
  const content = document.createElement('div');
  content.textContent = evt.content;
  div.appendChild(label);
  div.appendChild(content);

  panel.insertBefore(div, waiting);
  panel.scrollTop = panel.scrollHeight;
}

// ── Screen transitions ─────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ── Start button ───────────────────────────────────────
document.getElementById('btn-start').addEventListener('click', async () => {
  try {
    const res = await fetch('/scenario');
    scenario = await res.json();
  } catch (err) {
    alert('Failed to load scenario. Is the server running?');
    return;
  }
  document.getElementById('intro-text').textContent = scenario.intro;
  document.getElementById('intro-goal-text').textContent = scenario.goal;
  showScreen('screen-intro');
});

// ── Begin button ───────────────────────────────────────
document.getElementById('btn-begin').addEventListener('click', () => {
  const container = document.getElementById('gradio-container');
  if (!document.getElementById('gradio-iframe')) {
    const iframe = document.createElement('iframe');
    iframe.src = '/gradio';
    iframe.id = 'gradio-iframe';
    container.appendChild(iframe);
  }

  actionsTaken = new Set();
  gameOver = false;
  document.getElementById('transcript').innerHTML = `
    <div id="waiting">
      <div class="dot-pulse">
        <span></span><span></span><span></span>
      </div>
      Waiting for patient response…
    </div>`;
  document.getElementById('action-badge').style.display = 'none';
  document.getElementById('timer').textContent = formatTime(scenario.time_limit || 300);
  document.getElementById('timer').classList.remove('urgent');

  initLayers(scenario);

  showScreen('screen-game');
  wsSend({ type: 'begin' });
});

function formatTime(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

// ── End screen ─────────────────────────────────────────
function showEndScreen(status, reason) {
  const titleEl = document.getElementById('end-title');
  titleEl.textContent = status === 'success' ? 'De-escalation Successful!' : 'Simulation Ended';
  titleEl.className = status;

  document.getElementById('end-reason').textContent = reason || '';

  const list = document.getElementById('actions-checklist');
  list.innerHTML = '';
  if (scenario && scenario.actions) {
    scenario.actions.forEach(action => {
      const taken = actionsTaken.has(action.type);
      const isBad = action.point_change > 0;

      const item = document.createElement('div');
      item.className = 'action-item ' + (taken ? (isBad ? 'bad-found' : 'found') : 'missed');

      const icon = document.createElement('div');
      icon.className = 'action-icon';
      if (!taken)      icon.textContent = '○';
      else if (isBad)  icon.textContent = '✗';
      else             icon.textContent = '✓';

      const info = document.createElement('div');
      info.className = 'action-info';
      const name = document.createElement('div');
      name.className = 'action-name';
      name.textContent = action.type;
      const desc = document.createElement('div');
      desc.className = 'action-desc';
      desc.textContent = action.desc;
      info.appendChild(name);
      info.appendChild(desc);

      const delta = document.createElement('div');
      delta.className = 'action-delta ' + (action.point_change < 0 ? 'negative' : 'positive');
      delta.textContent = (action.point_change > 0 ? '+' : '') + action.point_change;

      item.appendChild(icon);
      item.appendChild(info);
      item.appendChild(delta);
      list.appendChild(item);
    });
  }

  document.getElementById('screen-end').style.display = 'flex';
}

// ── Play Again button ──────────────────────────────────
document.getElementById('btn-again').addEventListener('click', () => {
  document.getElementById('gradio-iframe')?.remove();
  document.getElementById('screen-end').style.display = 'none';

  layerElements = {};
  document.getElementById('scene-layers').innerHTML = '';

  actionsTaken = new Set();
  gameOver = false;
  document.getElementById('esc-fill').style.width = '50%';
  document.getElementById('esc-value').textContent = '5 / 10';

  wsSend({ type: 'reset' });
  showScreen('screen-start');
});

// ── Init ───────────────────────────────────────────────
connectWS();
