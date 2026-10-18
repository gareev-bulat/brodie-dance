// Brodie's one line, live, for a signed-in visitor: polls GET /api/line every few seconds (the server is the truth) and ticks the
// countdown every second in between, correcting itself on each poll. BrodieLine.watch(api, onChange) calls
// onChange({state, left, text, timer, facetime, dial}) on every change; state is open | busy | mine | offline | signedout.
// mine: this visitor clicked "FaceTime Brodie" and the session is theirs. apply(d) takes a claim/done answer at once; stop() on log out.
window.BrodieLine = (() => {
  const POLL_MS = 5000, ENDING_S = 60;
  const mmss = (s) => { s = Math.max(0, Math.ceil(s)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); };
  const TEXT = {
    open: 'Brodie is free! FaceTime Brodie now and start learning the moves.',
    busy: "Someone's dancing with Brodie right now. Keep this page open: it updates by itself the moment he's free.",
    ending: 'Brodie should be free in',
    precall: "Brodie's yours! FaceTime him now. If no call starts, the line goes to the next person in",
    mine: "Brodie's yours! FaceTime him now and start learning the moves.",
    offline: 'Brodie is offline right now. Check back soon.',
    down: "Brodie's FaceTime line is having trouble right now. We're on it, check back soon.",
  };
  function view(state, left, facetime, dial, onCall = true, reason = '') {
    // busy: a call frees the line ~15 s after the caller hangs up, so the 15-minute session end is only a worst case: no
    // countdown from 15:00 (it sends people away); the countdown shows only for the last minute (the cooldown after a call).
    if (state === 'busy') return left <= ENDING_S
      ? { state, left, text: TEXT.ending, timer: mmss(left), facetime: '', dial: '' }
      : { state, left, text: TEXT.busy, timer: '', facetime: '', dial: '' };
    if (state === 'mine' && !onCall) return { state, precall: true, left, text: TEXT.precall, timer: mmss(left), facetime, dial };
    if (state === 'mine') return { state, left, text: TEXT.mine, timer: mmss(left), facetime, dial };
    if (state === 'open') return { state, left: 0, text: TEXT.open, timer: '', facetime, dial };
    if (state === 'offline') return { state, reason, left: 0, text: reason === 'down' ? TEXT.down : TEXT.offline, timer: '', facetime: '', dial: '' };
    return { state: 'signedout', left: 0, text: '', timer: '', facetime: '', dial: '' };
  }
  function watch(api, onChange, query = '') {
    let state = 'signedout', freeAt = 0, facetime = '', dialNum = '', onCall = true, reason = '', last = '', polling = false, polled = 0;
    const emit = () => {
      let left = (freeAt - Date.now()) / 1000;
      if ((state === 'busy' || state === 'mine') && left <= 0) {      // at zero: free on the page now, then the server confirms
        left = 0; state = 'open'; if (!polling && Date.now() - polled > 2000) poll();
      }
      const v = view(state, left, facetime, dialNum, onCall, reason), key = v.state + v.timer + v.facetime + !!v.precall + reason;
      if (key !== last) { last = key; onChange(v); }
    };
    function apply(d) {
      state = d.state; freeAt = Date.now() + (d.seconds_left || 0) * 1000; facetime = d.facetime || ''; dialNum = d.facetime_dial || ''; onCall = d.on_call !== false; reason = d.reason || '';
      emit();
    }
    async function poll() {
      polling = true; polled = Date.now();
      try {
        const r = await fetch(api + '/api/line' + query, { cache: 'no-store', credentials: 'same-origin' });
        if (r.status === 401) { state = 'signedout'; }
        else { const d = await r.json(); if (r.ok) { polling = false; return apply(d); } }
      } catch (e) { /* keep the last known state and keep ticking */ }
      polling = false; emit();
    }
    poll();
    const timers = [setInterval(poll, POLL_MS), setInterval(emit, 250)];
    return { poll, apply, stop() { timers.forEach(clearInterval); } };
  }
  return { watch, mmss, TEXT };
})();
