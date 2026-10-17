// The "FaceTime Brodie now" pop-up: opens the moment a visitor lands, so they can call in one tap.
//
//   BrodiePopup.mount({ call, done, start, waitlist, hasEmail })   once, from index.html
//   BrodiePopup.update(v)       every change of Brodie's line (the same view BrodieLine.watch gives the hero box)
//   BrodiePopup.needEmail()     the server wants an email before it shows the line (no guest sessions there)
//   BrodiePopup.open() / close()
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
  @keyframes bp-fade { from { opacity: 0; } }
  @keyframes bp-up { from { opacity: 0; transform: translateY(28px) scale(0.92); } }
  .bp.open .bp-dot, .bp.mine .bp-dot { animation: bp-live 1.6s ease-out infinite; }
  @keyframes bp-live { from { box-shadow: 0 0 0 0 rgba(217,255,61,0.7); } to { box-shadow: 0 0 0 10px rgba(217,255,61,0); } }
  .bp-status.freed { animation: bp-freed .8s cubic-bezier(.2, 1.5, .4, 1); }
  @keyframes bp-freed { 30% { transform: scale(1.08); box-shadow: 0 0 0 3px var(--volt), 0 0 36px rgba(217,255,61,0.5); } }
  @keyframes bp-pulse { to { opacity: 0.3; } }
  @media (max-width: 520px) { .bp { padding: 22px 18px 18px; } .bp h2 { font-size: 1.7rem; } .bp img.bp-face { width: 72px; height: 72px; } }
  @media (prefers-reduced-motion: reduce) { .bp-back, .bp, .bp-status.freed { animation: none; } .bp .bp-dot { animation: none !important; } }`;

  let opts = {}, el = {}, v = { state: 'loading', left: 0 }, freeAt = 0, mode = 'line', sawCall = false, lastFocus = null, qrDone = false;
  const $ = (sel) => el.root.querySelector(sel);
  const mins = (s) => Math.max(1, Math.ceil(s / 60));

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
      <p class="bp-sub">Scan with your iPhone camera. This page opens there with a one-tap FaceTime button.</p>
      <p class="bp-fine">Brodie is on FaceTime, so you need an iPhone, iPad or Mac.</p>`;
  }

  async function shareLink() {
    const url = location.origin + '/';
    try { if (navigator.share) { await navigator.share({ title: 'Brodie Dance', text: 'FaceTime Brodie, your dance coach', url }); return; } } catch (e) { return; }
    try { await navigator.clipboard.writeText(url); $('.bp-share').textContent = 'link copied'; } catch (e) { $('.bp-share').textContent = url; }
  }

  function qr() {
    if (qrDone || device !== 'desktop') return; qrDone = true;
    const draw = () => {
      const q = qrcode(0, 'M'); q.addData(location.origin + '/?from=qr'); q.make();
      $('.bp-qr').innerHTML = q.createSvgTag({ cellSize: 6, margin: 0, scalable: true });
    };
    if (window.qrcode) return draw();
    const s = document.createElement('script'); s.src = 'vendor/qrcode.js'; s.onload = draw; document.head.append(s);
  }

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
    if (s === 'mine' && v.precall) return ['mine', `He's yours! Call within ${BrodieLine.mmss(left)}.`, 'After that the line goes to the next person.'];
    if (s === 'mine') return ['mine', "You're on with Brodie. Have fun!", ''];
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
      go.disabled = !(v.state === 'open' || v.state === 'mine');
      go.textContent = v.state === 'busy' ? "Brodie's busy" : v.state === 'offline' ? 'Brodie is offline' : 'FaceTime Brodie now';
    }
    if (!el.root.hidden) qr();
  }

  function update(nv) {
    if (nv.state === 'signedout') return;
    const was = v; v = nv; freeAt = Date.now() + (nv.left || 0) * 1000;
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

  function open() {
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

  return { mount, update, needEmail, open, close, device, apple };
})();
