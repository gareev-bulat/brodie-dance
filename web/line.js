// Brodie's one line, live: polls GET /api/line every few seconds (the server is the truth) and ticks the countdown every second
// in between, correcting itself on each poll. BrodieLine.watch(api, onChange) calls onChange({state, left, text, timer}) on every
// change; state is open | busy | offline | unknown (the line is not configured: callers behave as before).
window.BrodieLine = (() => {
  const POLL_MS = 5000;
  const mmss = (s) => { s = Math.max(0, Math.ceil(s)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); };
  const busyText = (left) => {
    const m = Math.max(1, Math.round(left / 60));
    return "Brodie's teaching someone right now. Come back in about " + m + ' minute' + (m === 1 ? '' : 's') + '.';
  };
  function view(state, left) {
    if (state === 'busy') return { state, left, text: busyText(left), timer: mmss(left) };
    if (state === 'offline') return { state, left: 0, text: 'Brodie is offline.', timer: '' };
    if (state === 'open') return { state, left: 0, text: 'Line open — call Brodie now', timer: '' };
    return { state: 'unknown', left: 0, text: '', timer: '' };
  }
  function watch(api, onChange) {
    let state = 'unknown', freeAt = 0, timer = null, last = '';
    const emit = () => {
      let left = (freeAt - Date.now()) / 1000;
      if (state === 'busy' && left <= 0) { state = 'open'; left = 0; poll(); }   // at zero: open now, and ask the server to confirm
      const v = view(state, left), key = v.state + v.timer;
      if (key !== last) { last = key; onChange(v); }
    };
    async function poll() {
      try {
        const r = await fetch(api + '/api/line', { cache: 'no-store' });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || r.status);
        state = d.enabled === false && d.state === 'open' ? 'unknown' : d.state;
        freeAt = Date.now() + (d.seconds_left || 0) * 1000;
      } catch (e) { /* keep the last known state and keep ticking */ }
      emit();
    }
    poll(); setInterval(poll, POLL_MS); timer = setInterval(emit, 250);
    return {
      // a 409 from /api/token carries the same status: show it at once without waiting for the next poll
      busy(d) { state = 'busy'; freeAt = Date.now() + (d.seconds_left || 0) * 1000; emit(); },
      get state() { return state; },
    };
  }
  return { watch, mmss, busyText };
})();
