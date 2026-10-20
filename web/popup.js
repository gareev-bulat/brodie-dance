// The "FaceTime Brodie now" pop-up: opens the moment a visitor lands, so they can call in one tap.
//
//   BrodiePopup.mount({ call, done, start, waitlist, hasEmail, pick })   once, from index.html
//   BrodiePopup.update(v)       every change of Brodie's line (the same view BrodieLine.watch gives the hero box)
//   BrodiePopup.needEmail()     the server wants an email before it shows the line (no guest sessions there)
//   BrodiePopup.open({dance}?) / close()   dance: open with that lesson picked
//   BrodiePopup.setDances([{name, title}], def)   the lessons the live worker can teach (/api/config pick): with two or more,
//                               tiles to pick one (from ?dance=, else the last pick in this browser, else def); fewer: no picker
//   BrodiePopup.pick() / select(name, byUser)    the picked lesson id ('' with no picker); a pick by the visitor calls pick(name)
//
// What it shows depends on the device (BrodiePopup.device, ?device=ios|mac|android|desktop overrides it for testing):
//   iPhone, iPad, Mac  one big button that opens FaceTime with a facetime: link (the call() callback takes the line first)
//   Android            an honest note: FaceTime calls only start from Apple devices; a button to send the link to one
//   other computers    a QR code: scan it with an iPhone, the page opens there with the same pop-up
// The line's state is live: free, busy ("free in about 1 minute"), offline, or yours (a countdown to start the call).
// After a call ends, a visitor without an email on file is asked, optionally, for one (the new-dance waitlist).
window.BrodiePopup = (() => {
  const Q = new URLSearchParams(location.search);
  const device = (() => {
    const forced = Q.get('device');
    if (['ios', 'mac', 'android', 'desktop'].includes(forced)) return forced;
    const ua = navigator.userAgent;
    if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return 'ios';   // iPadOS says Macintosh
    if (/Macintosh/.test(ua)) return 'mac';
    if (/Android/.test(ua)) return 'android';
    return 'desktop';
  })();
  const apple = device === 'ios' || device === 'mac';
  const SEEN = 'brodie_popup_closed';
  const TITLE = apple ? 'facetime brodie now' : device === 'android' ? "brodie's on facetime" : 'call brodie from your iphone';
  const css = `
  .bp-back { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center; padding: 16px; background: rgba(10,10,11,0.78);
    -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px); animation: bp-fade .18s ease; }
  .bp-back[hidden] { display: none; }
  .bp { position: relative; width: min(440px, 100%); max-height: calc(100vh - 32px); overflow-y: auto; background: var(--panel); border: 2px solid var(--volt);
    border-radius: 24px; padding: 26px 24px 22px; text-align: center; box-shadow: 0 30px 80px rgba(0,0,0,0.6); animation: bp-up .42s cubic-bezier(.2, 1.4, .4, 1); }
  .bp-x { position: absolute; top: 10px; right: 10px; width: 40px; height: 40px; border-radius: 50%; border: 0; background: transparent; color: var(--grey);
    font-size: 1.6rem; line-height: 1; cursor: pointer; }
  .bp-x:hover { color: var(--text); background: var(--line); }
  .bp img.bp-face { width: 88px; height: 88px; border-radius: 50%; object-fit: cover; border: 3px solid var(--volt); margin: 0 auto 12px; display: block; }
  .bp h2 { font-size: 2rem; margin: 0 0 14px; }
  .bp-status { display: inline-flex; align-items: center; gap: 10px; padding: 10px 16px; border-radius: 999px; background: var(--ground); border: 1px solid var(--line);
    font-weight: 600; font-size: 1rem; line-height: 1.3; text-align: left; }
  .bp-dot { width: 11px; height: 11px; border-radius: 50%; background: var(--grey); flex: none; }
  .bp.open .bp-dot, .bp.mine .bp-dot { background: var(--volt); box-shadow: 0 0 10px var(--volt); }
  .bp.busy .bp-dot { background: #FFB020; }
  .bp.offline .bp-dot { background: #FF6B6B; }
  .bp.loading .bp-dot { animation: bp-pulse 1s ease-in-out infinite alternate; }
  .bp-sub { color: #CFCBC3; font-size: 0.95rem; margin: 12px auto 0; max-width: 30em; }
  .bp-go { display: flex; width: 100%; justify-content: center; margin-top: 18px; font-size: 1.35rem; padding: 20px 24px; border-radius: 16px; cursor: pointer; }
  .bp-go:disabled { background: var(--line); border-color: var(--line); color: var(--grey); cursor: default; transform: none; }
  .bp-done { margin-top: 12px; font-size: 0.95rem; padding: 10px 18px; background: none; cursor: pointer; }
  .bp-fine { font-size: 0.85rem; color: var(--grey); margin: 12px auto 0; max-width: 30em; }
  .bp-wait { font-size: 0.92rem; line-height: 1.4; margin: 14px auto 0; max-width: 28em; padding: 10px 14px; border-radius: 12px;
             border: 1px dashed var(--volt); color: var(--text, #EFE7DA); }
  .bp-wait b { color: var(--volt); }
  .bp-wait[hidden] { display: none; }
  .bp-trust { display: flex; align-items: center; justify-content: center; gap: 6px; margin-top: 6px; color: #CFCBC3; }
  .bp-trust svg { width: 14px; height: 14px; fill: none; stroke: var(--volt); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .bp-qr { margin: 18px auto 0; width: 220px; height: 220px; padding: 12px; background: #fff; border-radius: 16px; }
  .bp-qr svg, .bp-qr img { width: 100%; height: 100%; display: block; }
  .bp-note { margin-top: 18px; padding: 16px 18px; border-radius: 14px; background: var(--ground); border: 1px solid var(--line); text-align: left; font-size: 0.98rem; }
  .bp-note p { margin: 0 0 8px; }
  .bp-note .btn { background: transparent; color: var(--text); margin-top: 6px; cursor: pointer; }
  .bp.unknown .bp-status { display: none; }
  .bp form { display: flex; flex-direction: column; gap: 10px; margin-top: 18px; }
  .bp input { width: 100%; padding: 15px 16px; border-radius: 12px; border: 1px solid var(--line); background: var(--ground); color: var(--text); font: inherit; font-size: 1.05rem; }
  .bp input:focus { outline: 2px solid var(--volt); outline-offset: -1px; }
  .bp form .btn { justify-content: center; cursor: pointer; }
  .bp-skip { margin-top: 4px; background: none; border: 0; color: var(--grey); font: inherit; font-size: 0.9rem; cursor: pointer; text-decoration: underline; }
  .bp-err { color: #FF8A8A; font-size: 0.9rem; min-height: 1.2em; margin: 6px 0 0; }
  .bp-pick { margin: 16px 0 0; }
  .bp-pick-label { margin: 0 0 8px; font-size: 0.85rem; font-weight: 600; color: var(--grey); }
  .bp-tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
  .bp-dance { min-height: 48px; padding: 8px 6px; white-space: normal; text-align: center; font: inherit; line-height: 1.15; font-size: 0.9rem; font-weight: 600;
    color: var(--text); background: rgba(244,242,238,0.06); border: 1px solid var(--line); border-radius: 14px; cursor: pointer; }
  .bp-dance:hover { border-color: rgba(217,255,61,0.5); }
  .bp-dance[aria-pressed="true"] { border-color: var(--volt); background: rgba(217,255,61,0.12); color: var(--volt); }
  .bp-dance:focus-visible { outline: 2px solid var(--volt); outline-offset: 2px; }
  @media (max-height: 680px) { .bp.picking img.bp-face { display: none; } }   /* room for the tiles on short screens */
  @keyframes bp-fade { from { opacity: 0; } }
  @keyframes bp-up { from { opacity: 0; transform: translateY(28px) scale(0.92); } }
  .bp.open .bp-dot, .bp.mine .bp-dot { animation: bp-live 1.6s ease-out infinite; }
  @keyframes bp-live { from { box-shadow: 0 0 0 0 rgba(217,255,61,0.7); } to { box-shadow: 0 0 0 10px rgba(217,255,61,0); } }
  .bp-status.freed { animation: bp-freed .8s cubic-bezier(.2, 1.5, .4, 1); }
  @keyframes bp-freed { 30% { transform: scale(1.08); box-shadow: 0 0 0 3px var(--volt), 0 0 36px rgba(217,255,61,0.5); } }
  @keyframes bp-pulse { to { opacity: 0.3; } }
  @media (max-width: 520px) { .bp { padding: 22px 18px 18px; } .bp h2 { font-size: 1.7rem; } .bp img.bp-face { width: 72px; height: 72px; } }
  @media (prefers-reduced-motion: reduce) { .bp-back, .bp, .bp-status.freed { animation: none; } .bp .bp-dot { animation: none !important; } }`;

  let opts = {}, el = {}, v = { state: 'loading', left: 0 }, freeAt = 0, mode = 'line', sawCall = false, lastFocus = null;
  let dances = [], sel = '', qrDrawn = '', qrLoad = null;     // the pickable lessons [{name, title}], the picked one, the URL in the QR
  const $ = (sel) => el.root.querySelector(sel);
  const mins = (s) => Math.max(1, Math.ceil(s / 60));
  const ID = /^[a-z0-9_]{1,40}$/, PICKED = 'brodie-dance';
  const valid = (n) => typeof n === 'string' && ID.test(n) && dances.some((d) => d.name === n);
  const titleOf = (n) => (dances.find((d) => d.name === n) || {}).title || '';

  function mount(o) {
    opts = o;
    const style = document.createElement('style'); style.textContent = css; document.head.append(style);
    el.root = document.createElement('div'); el.root.className = 'bp-back'; el.root.hidden = true;
    el.root.innerHTML = `
      <div class="bp loading" role="dialog" aria-modal="true" aria-labelledby="bp-title">
        <button type="button" class="bp-x" aria-label="close">&times;</button>
        <img class="bp-face" src="assets/brodie/brodie-pfp-square.png" alt="">
        <h2 id="bp-title">${TITLE}</h2>
        <div class="bp-line">
          <span class="bp-status" aria-live="polite"><i class="bp-dot"></i><span class="bp-text">Checking if Brodie is free…</span></span>
          <p class="bp-sub" hidden></p>
          <div class="bp-pick" role="group" aria-label="pick your dance" hidden><p class="bp-pick-label">pick your dance</p><div class="bp-tiles"></div></div>
          <div class="bp-act"></div>
        </div>
        <div class="bp-email" hidden>
          <p class="bp-sub">Enter your email to see if Brodie is free.</p>
          <form class="bp-start"><input type="email" required placeholder="your email" autocomplete="email" aria-label="your email">
            <button class="btn primary" type="submit">see if brodie is free →</button></form>
          <p class="bp-err" aria-live="polite"></p>
          <p class="bp-fine">Just your email. No password, no app.</p>
        </div>
        <div class="bp-after" hidden>
          <p class="bp-sub">Nice moves! Want an email when a new dance drops? Totally optional.</p>
          <form class="bp-wl"><input type="email" required placeholder="your email" autocomplete="email" aria-label="your email for new dances">
            <button class="btn primary" type="submit">email me new dances</button></form>
          <button type="button" class="bp-skip">no thanks</button>
          <p class="bp-err" aria-live="polite"></p>
        </div>
      </div>`;
    document.body.append(el.root);
    el.card = $('.bp'); el.text = $('.bp-text'); el.sub = $('.bp-line .bp-sub'); el.act = $('.bp-act');
    el.act.innerHTML = actions();
    $('.bp-x').onclick = () => close(true);
    el.root.addEventListener('click', (e) => { if (e.target === el.root) close(true); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !el.root.hidden) close(true); });
    const go = $('.bp-go');
    if (go) go.onclick = async () => { go.disabled = true; try { await opts.call(); } finally { paint(); } };
    const done = $('.bp-done');
    if (done) done.onclick = async () => { done.disabled = true; try { await opts.done(); } finally { done.disabled = false; } };
    const share = $('.bp-share');
    if (share) share.onclick = shareLink;
    $('.bp-start').onsubmit = async (e) => {
      e.preventDefault(); const f = e.target, err = $('.bp-email .bp-err'); err.textContent = ''; f.querySelector('button').disabled = true;
      try { const msg = await opts.start(f.querySelector('input').value); if (msg) err.textContent = msg; else showMode('line'); }
      finally { f.querySelector('button').disabled = false; }
    };
    $('.bp-wl').onsubmit = async (e) => {
      e.preventDefault(); const f = e.target, err = $('.bp-after .bp-err'); err.textContent = ''; f.querySelector('button').disabled = true;
      try { const msg = await opts.waitlist(f.querySelector('input').value); if (msg) err.textContent = msg; else { $('.bp-after').innerHTML = '<p class="bp-sub">You\'re in. We\'ll email you when a new dance drops.</p>'; } }
      finally { f.querySelector('button').disabled = false; }
    };
    $('.bp-skip').onclick = () => close(true);
    tiles();
    setInterval(paint, 1000);                       // the "free in about N minutes" text and the call countdown tick between polls
    let opened = false; try { opened = sessionStorage.getItem(SEEN) === '1'; } catch (e) {}
    if (!opened) open();
  }

  function actions() {
    if (apple) return `<button type="button" class="btn primary bp-go" disabled>FaceTime Brodie now</button>
      <button type="button" class="btn ghost bp-done" hidden>I'm done dancing</button>
      <p class="bp-wait"><b>Brodie takes a few seconds to pick up.</b> Stay on, he's loading your next viral video 🎬</p>
      <p class="bp-fine bp-applefine">Opens FaceTime. Prop your phone up and step back so you fit in the frame.</p>
      <p class="bp-fine bp-trust"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2.5"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>Never saved. No account needed.</p>`;
    if (device === 'android') return `<div class="bp-note"><p><b>Brodie teaches over FaceTime, and FaceTime calls can only be started from an iPhone, iPad or Mac.</b></p>
      <p>Got one nearby? Open this page on it and tap the big button.</p>
      <button type="button" class="btn ghost bp-share">send this link to my iPhone</button></div>`;
    return `<div class="bp-qr" role="img" aria-label="QR code that opens this page on your phone"></div>
      <p class="bp-sub bp-qrcap">Scan with your iPhone camera. This page opens there with a one-tap FaceTime button.</p>
      <p class="bp-fine">Brodie is on FaceTime, so you need an iPhone, iPad or Mac.</p>`;
  }

  async function shareLink() {
    const url = location.origin + '/' + (sel ? '?dance=' + encodeURIComponent(sel) : '');     // the pick goes along to the iPhone
    try { if (navigator.share) { await navigator.share({ title: 'Brodie Dance', text: 'FaceTime Brodie, your dance coach', url }); return; } } catch (e) { return; }
    try { await navigator.clipboard.writeText(url); $('.bp-share').textContent = 'link copied'; } catch (e) { $('.bp-share').textContent = url; }
  }

  // the QR opens this page on the iPhone with the same pick (?dance=): redrawn when the pick changes. The desktop never claims the
  // line for the phone (another browser, another guest): the pick only rides along in the link
  function qr() {
    if (device !== 'desktop' || !el.root || el.root.hidden) return;
    const url = location.origin + '/?from=qr' + (sel ? '&dance=' + encodeURIComponent(sel) : '');
    if (qrDrawn === url) return; qrDrawn = url;
    const draw = () => {
      if (qrDrawn !== url || !window.qrcode) return;               // a newer pick won while the library loaded
      const q = qrcode(0, 'M'); q.addData(url); q.make();
      const box = $('.bp-qr'), t = titleOf(sel), cap = $('.bp-qrcap');
      box.innerHTML = q.createSvgTag({ cellSize: 6, margin: 0, scalable: true }); box.dataset.url = url;
      box.setAttribute('aria-label', 'QR code that opens this page on your phone' + (t ? ' with ' + t + ' picked' : ''));
      if (cap) cap.textContent = 'Scan with your iPhone camera. This page opens there with ' + (t ? t + ' picked and ' : '') + 'a one-tap FaceTime button.';
    };
    if (window.qrcode) return draw();
    if (!qrLoad) qrLoad = new Promise((res, rej) => { const s = document.createElement('script'); s.src = 'vendor/qrcode.js'; s.onload = res; s.onerror = rej; document.head.append(s); });
    qrLoad.then(draw, () => {});
  }

  // ---- the dance picker: tiles above the button; the pick goes with the claim (index.html) and rides along in the QR / share link ----
  function setDances(list, def) {
    const seen = new Set();
    dances = (Array.isArray(list) ? list : []).filter((d) => d && typeof d.name === 'string' && ID.test(d.name) && !seen.has(d.name) && seen.add(d.name))
      .map((d) => ({ name: d.name, title: String(d.title || d.name).slice(0, 60) }));
    if (dances.length < 2) { dances = []; sel = ''; tiles(); paint(); return; }
    const url = Q.get('dance'); let kept = '';
    try { kept = localStorage.getItem(PICKED) || ''; } catch (e) {}
    sel = [url, kept, def, dances[0].name].find(valid);
    if (sel === url) { try { localStorage.setItem(PICKED, sel); } catch (e) {} }   // a pick from the QR / share link survives a reload
    try {                                                            // no ?dance / ?from left in the address bar (or in a shared link)
      if (Q.has('dance') || Q.has('from')) { const u = new URL(location.href); u.searchParams.delete('dance'); u.searchParams.delete('from'); history.replaceState(history.state, '', u.pathname + u.search + u.hash); }
    } catch (e) {}
    tiles(); paint();
  }

  function tiles() {
    if (!el.root) return;
    const box = $('.bp-tiles'); box.textContent = '';
    box.style.gridTemplateColumns = 'repeat(' + (dances.length === 4 ? 2 : Math.min(dances.length, 3)) + ', minmax(0, 1fr))';   // four: 2 x 2
    for (const d of dances) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'bp-dance'; b.dataset.dance = d.name;
      b.textContent = d.title; b.setAttribute('aria-pressed', String(d.name === sel)); b.onclick = () => select(d.name, true);
      box.append(b);
    }
  }

  // byUser: the visitor tapped (a tile, or a card's "play it with brodie"): remembered, and opts.pick tells the page (a re-claim
  // before the call). Otherwise the page follows the server's view of their own line
  function select(name, byUser) {
    if (!valid(name)) return false;
    const changed = name !== sel; sel = name;
    try { localStorage.setItem(PICKED, name); } catch (e) {}
    if (el.root) for (const b of el.root.querySelectorAll('.bp-dance')) b.setAttribute('aria-pressed', String(b.dataset.dance === name));
    paint();
    if (byUser && opts.pick) Promise.resolve().then(() => opts.pick(name)).catch(() => {});
    return changed;
  }

  function pick() { return sel; }

  function showMode(m) {
    if (!el.root) return;
    mode = m;
    $('.bp-line').hidden = m !== 'line'; $('.bp-email').hidden = m !== 'email'; $('.bp-after').hidden = m !== 'after';
    $('#bp-title').textContent = m === 'after' ? 'thanks for dancing' : TITLE;
    paint();
  }

  // the line's text, from the last view and the time now
  function status() {
    const left = Math.max(0, (freeAt - Date.now()) / 1000), s = v.state;
    if (s === 'open') return ['open', 'Brodie is free right now.', ''];
    if (s === 'busy') return ['busy', left <= 90 ? 'Busy right now. Free in about 1 minute.' : `Busy right now. Free in under ${mins(left)} minutes.`,
      'This updates by itself, no need to refresh.'];
    const song = s === 'mine' ? titleOf(v.dance) : '';               // the lesson the server holds for them ('' without a picker)
    if (s === 'mine' && v.precall) return ['mine', `He's yours! Call within ${BrodieLine.mmss(left)}${song ? ' to learn ' + song : ''}.`, 'After that the line goes to the next person.'];
    if (s === 'mine') return ['mine', song ? `You're on with Brodie to learn ${song}. Have fun!` : "You're on with Brodie. Have fun!", ''];
    if (s === 'offline' && v.reason === 'down') return ['offline', "Brodie's FaceTime line is having trouble.", "We're on it. Check back soon."];
    if (s === 'offline') return ['offline', 'Brodie is offline right now.', 'Check back soon.'];
    if (s === 'unknown') return ['unknown', 'Brodie teaches over FaceTime.', ''];
    return ['loading', 'Checking if Brodie is free…', ''];
  }

  function paint() {
    if (!el.root) return;
    const [cls, text, sub] = status();
    el.card.className = 'bp ' + cls;
    el.text.textContent = text; el.sub.textContent = sub; el.sub.hidden = !sub;
    const go = $('.bp-go'), done = $('.bp-done');
    if (go) {
      const onCall = v.state === 'mine' && !v.precall;
      go.hidden = onCall; done.hidden = !onCall; $('.bp-applefine').hidden = onCall; $('.bp-trust').hidden = onCall;
      const wait = $('.bp-wait'); if (wait) wait.hidden = onCall || v.state === 'offline';   // no "stay on" tip while nobody can call
      go.disabled = !(v.state === 'open' || v.state === 'mine');
      go.textContent = v.state === 'busy' ? "Brodie's busy" : v.state === 'offline' ? 'Brodie is offline' : 'FaceTime Brodie now';
    }
    const picking = dances.length > 1 && v.state !== 'offline' && !(v.state === 'mine' && !v.precall);   // not while on the call
    $('.bp-pick').hidden = !picking; el.card.classList.toggle('picking', picking);
    if (!el.root.hidden) qr();
  }

  function update(nv) {
    if (nv.state === 'signedout') return;
    const was = v; v = nv; freeAt = Date.now() + (nv.left || 0) * 1000;
    if (nv.state === 'mine' && nv.dance && nv.dance !== sel && valid(nv.dance)) select(nv.dance, false);   // follow the server's pick for them
    if (el.root && was.state === 'busy' && nv.state === 'open') {          // he just freed up while the pop-up was open: a little pop
      const st = $('.bp-status'); st.classList.remove('freed'); void st.offsetWidth; st.classList.add('freed');
    }
    if (nv.state === 'mine' && !nv.precall) sawCall = true;
    if (sawCall && nv.state !== 'mine' && was.state === 'mine') {   // this visitor's call just ended
      sawCall = false;
      if (!opts.hasEmail()) { showMode('after'); open(); return; }
    }
    if (mode === 'email') showMode('line'); else paint();
  }

  function needEmail() {
    if (!el.root) return;
    if (apple) showMode('email');      // Android and other computers can't call from here anyway: no email asked
    else { v = { state: 'unknown' }; paint(); }
  }

  function open(o) {
    if (o && o.dance) select(o.dance, true);                         // a card's "play it with brodie": that lesson picked
    if (!el.root || !el.root.hidden) return;
    lastFocus = document.activeElement; el.root.hidden = false; document.body.style.overflow = 'hidden'; paint();
    setTimeout(() => (mode === 'email' ? $('.bp-start input') : $('.bp-go') || $('.bp-x')).focus({ preventScroll: true }), 50);
  }

  function close(byUser) {
    if (!el.root || el.root.hidden) return;
    el.root.hidden = true; document.body.style.overflow = '';
    if (byUser) { try { sessionStorage.setItem(SEEN, '1'); } catch (e) {} }
    if (mode === 'after') showMode('line');
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }

  return { mount, update, needEmail, open, close, setDances, pick, select, device, apple };
})();
