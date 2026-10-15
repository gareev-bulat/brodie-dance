// Brodie's one line, live, for a signed-in visitor: polls GET /api/line every few seconds (the server is the truth) and ticks the
// countdown every second in between, correcting itself on each poll. BrodieLine.watch(api, onChange) calls
// onChange({state, left, text, timer, facetime}) on every change; state is open | busy | offline | signedout.
// The call stops polling with stop() (on log out).
window.BrodieLine = (() => {
  const POLL_MS = 5000;
  const mmss = (s) => { s = Math.max(0, Math.ceil(s)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); };
  const TEXT = {
    open: 'Brodie is free! FaceTime Brodie now and start learning the moves.',
    busy: (m) => 'Brodie is in a session with another person right now. Please wait about ' + m + ' minute' + (m === 1 ? '' : 's') + ' and come back to try again.',
    offline: 'Brodie is offline right now. Check back soon.',
  };
  function view(state, left, facetime, dial) {
    if (state === 'busy') return { state, left, text: TEXT.busy(Math.max(1, Math.ceil(left / 60))), timer: mmss(left), facetime: '' };
    if (state === 'open') return { state, left: 0, text: TEXT.open, timer: '', facetime, dial };
    if (state === 'offline') return { state, left: 0, text: TEXT.offline, timer: '', facetime: '' };
    return { state: 'signedout', left: 0, text: '', timer: '', facetime: '' };
  }
  function watch(api, onChange, query = '') {
    let state = 'signedout', freeAt = 0, facetime = '', dialNum = '', last = '', polling = false, polled = 0;
    const emit = () => {
      let left = (freeAt - Date.now()) / 1000;
      if (state === 'busy' && left <= 0) { left = 0; if (!polling && Date.now() - polled > 2000) poll(); }   // at zero: ask the server (free now, or a new session)
      const v = view(state, left, facetime, dialNum), key = v.state + v.timer + v.facetime;
      if (key !== last) { last = key; onChange(v); }
    };
    async function poll() {
      polling = true; polled = Date.now();
      try {
        const r = await fetch(api + '/api/line' + query, { cache: 'no-store', credentials: 'same-origin' });
        if (r.status === 401) { state = 'signedout'; }
        else {
          const d = await r.json();
          if (r.ok) { state = d.state; freeAt = Date.now() + (d.seconds_left || 0) * 1000; facetime = d.facetime || ''; dialNum = d.facetime_dial || ''; }
        }
      } catch (e) { /* keep the last known state and keep ticking */ }
      polling = false; emit();
    }
    poll();
    const timers = [setInterval(poll, POLL_MS), setInterval(emit, 250)];
    return { poll, stop() { timers.forEach(clearInterval); } };
  }
  return { watch, mmss, TEXT };
})();
