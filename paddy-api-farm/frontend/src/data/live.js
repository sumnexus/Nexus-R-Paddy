// The live data source: the Python backend's REST API and its event stream.
// The backend serves this site itself, so paths are relative; set window.PADDY_API_BASE to use another server.
const BASE = (typeof window !== 'undefined' && window.PADDY_API_BASE ? String(window.PADDY_API_BASE) : '').replace(/\/$/, '');

async function req(method, path, body) {
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
    });
  } catch (e) {
    const err = new Error('The farm server is not answering.');
    err.offline = true;
    throw err;
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const err = new Error((data && data.error) || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function qs(params) {
  const p = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return p.length ? '?' + p.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&') : '';
}

export function createLiveSource() {
  return {
    kind: 'live',
    health: () => req('GET', '/api/health'),
    config: () => req('GET', '/api/config'),
    farm: () => req('GET', '/api/farm'),
    field: (name) => req('GET', `/api/fields/${name}`),
    weather: () => req('GET', '/api/weather'),
    squareHistory: (name, r, c) => req('GET', `/api/fields/${name}/squares/${r},${c}`),
    compareStart: (body) => req('POST', '/api/compare', body),
    compare: () => req('GET', '/api/compare'),
    decisions: (params) => req('GET', '/api/decisions' + qs(params)).then((r) => r.decisions),
    decision: (id) => req('GET', `/api/decisions/${id}`),
    approve: (id) => req('POST', `/api/decisions/${id}/approve`, {}),
    reject: (id, reason) => req('POST', `/api/decisions/${id}/reject`, { reason }),
    start: () => req('POST', '/api/sim/start', {}),
    pause: () => req('POST', '/api/sim/pause', {}),
    step: (hours) => req('POST', '/api/sim/step', { hours }),
    reset: (cfg) => req('POST', '/api/sim/reset', cfg),
    speed: (x) => req('POST', '/api/sim/speed', { x }),
    // v2: how fast arm work plays (simulated seconds per real second while an arm works; 0 = don't slow down)
    setArmRate: (v, speed) => req('POST', '/api/sim/speed', { x: speed || 24, work: v > 0 ? v : 3600 }),
    event: (body) => req('POST', '/api/events', body),
    observe: (body) => req('POST', '/api/observations', body),
    models: () => req('GET', '/api/models'),
    audit: () => req('GET', '/api/audit'),
    runs: () => req('GET', '/api/runs').then((r) => r.runs),
    exportUrl: (id, fmt) => `${BASE}/api/runs/${encodeURIComponent(id)}/export?format=${fmt}`,
    subscribe(onMessage, onConnection) {
      if (typeof EventSource === 'undefined') return () => {};
      let es = null;
      let closed = false;
      let retry = null;
      const open = () => {
        es = new EventSource(BASE + '/api/stream');
        es.onopen = () => onConnection && onConnection(true);
        es.onmessage = (ev) => {
          try { onMessage(JSON.parse(ev.data)); } catch (e) { /* ignore a bad frame */ }
        };
        es.onerror = () => {
          onConnection && onConnection(false);
          es.close();
          if (!closed) retry = setTimeout(open, 3000);
        };
      };
      open();
      return () => { closed = true; clearTimeout(retry); if (es) es.close(); };
    },
  };
}
