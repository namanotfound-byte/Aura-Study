/* AuraStudy landing + auth-page wordmark reveal.
 * Progressive enhancement only: every page this script touches is fully
 * usable (wordmark text, form fields, buttons -- all present and focusable)
 * with this script never running at all. What this file adds, for two
 * different contexts:
 *
 *   1. Landing page (`#letters`, full-size reveal): splits "AuraStudy" into
 *      individual letters so landing.css can stagger them in, then -- once
 *      the reveal has played -- sends an already-authenticated visitor to
 *      /app. The hero book-open replays on every visit unless the user prefers
 *      reduced motion. Auth-page letter reveals still skip after the first
 *      visit in a tab session.
 *
 *   2. Signed-out auth pages (`#auth-wordmark-letters`, base.html's brand
 *      header on login/register/forgot/reset): a smaller, quicker cousin of
 *      the same reveal. Same once-per-session rule, plus click/key/touch
 *      anywhere on the page jumps straight to the finished state -- these
 *      are pages people are trying to get *through*, not admire, so the
 *      reveal must never make them wait.
 *
 * Both contexts share:
 *   - prefers-reduced-motion: skip the per-letter animation entirely, show
 *     the finished state immediately (landing.css / auth.css's reduced-
 *     motion blocks do the actual CSS override; this file just avoids ever
 *     starting the animation and marks the session as "seen").
 *   - sessionStorage access wrapped in try/catch: private browsing or
 *     blocked storage must never break the page -- worst case, the reveal
 *     just plays again on the next visit instead of skipping.
 *
 * Landing timing constants are kept in step with landing.css's keyframe
 * animation-delay values (tagline at 1.05s, buttons at 1.15s+0.45s = 1.6s).
 * Auth timing constants are deliberately much shorter -- see landing.css's
 * neighbour, auth.css, for the matching keyframes.
 */
(function () {
  'use strict';

  var AUTH_SESSION_KEY = 'aurastudy:authWordmarkSeen';

  var prefersReducedMotion = !!(
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );

  function sessionSeen(key) {
    try {
      return window.sessionStorage.getItem(key) === '1';
    } catch (e) {
      // Private mode / blocked storage: treat as "not seen yet" so the
      // reveal simply plays -- never a blank or broken page.
      return false;
    }
  }

  function markSessionSeen(key) {
    try {
      window.sessionStorage.setItem(key, '1');
    } catch (e) {
      /* Best-effort only; if this silently fails the reveal just plays
         again next time instead of skipping. */
    }
  }

  function splitIntoLetters(host, baseDelay, stagger) {
    var text = host.textContent.trim();
    var frag = document.createDocumentFragment();

    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      var span = document.createElement('span');
      span.className = 'letter' + (ch === ' ' ? ' is-space' : '');
      span.style.setProperty('--delay', (baseDelay + i * stagger).toFixed(3) + 's');
      span.textContent = ch === ' ' ? ' ' : ch;
      frag.appendChild(span);
    }

    host.textContent = '';
    host.appendChild(frag);
    host.classList.add('is-split');
  }

  // ---------------- 1. Landing page (full reveal) ----------------

  var TOTAL_ANIMATION_MS = 2800; // book flip + settle + tagline/CTA fade-in
  var INSTANT_NAV_DELAY_MS = 250; // just enough for the finished title to register
  var BOOK_SETTLE_MS = 1850; // wordmark page finishes ~1.75s; settle shortly after

  function initLanding() {
    var brand = document.getElementById('landing-brand');
    if (!brand) return; // not the landing page

    var root = document.documentElement;
    var body = document.body;
    var authenticated = body.getAttribute('data-authenticated') === 'true';
    var wordmarkPage = brand.querySelector('.landing-book-page--wordmark');

    function goToApp() {
      window.location.href = '/app';
    }

    function finishBookHero() {
      root.classList.add('landing-book-settled');
    }

    if (prefersReducedMotion) {
      root.classList.add('skip-anim');
      root.classList.remove('landing-animate');
      if (authenticated) window.setTimeout(goToApp, INSTANT_NAV_DELAY_MS);
      if (window.AuraTour && typeof window.AuraTour.initLandingTour === 'function') {
        window.AuraTour.initLandingTour();
      }
      return;
    }

    if (wordmarkPage) {
      wordmarkPage.addEventListener('animationend', function onBookDone(evt) {
        if (evt.animationName !== 'landingBookPageRight') return;
        wordmarkPage.removeEventListener('animationend', onBookDone);
        finishBookHero();
      });
      window.setTimeout(finishBookHero, BOOK_SETTLE_MS);
    }

    if (authenticated) window.setTimeout(goToApp, TOTAL_ANIMATION_MS);

    if (window.AuraTour && typeof window.AuraTour.initLandingTour === 'function') {
      window.AuraTour.initLandingTour();
    }
  }

  // ---------------- 2. Auth pages (mini reveal) ----------------

  var AUTH_LETTER_BASE_DELAY = 0.015;
  var AUTH_LETTER_STAGGER = 0.022;

  function initAuthWordmark() {
    var host = document.getElementById('auth-wordmark-letters');
    if (!host) return; // not an auth page

    var root = document.documentElement;

    function finish() {
      root.classList.add('skip-anim');
    }

    if (prefersReducedMotion) {
      markSessionSeen(AUTH_SESSION_KEY);
      finish();
      return;
    }

    if (sessionSeen(AUTH_SESSION_KEY)) {
      finish();
      return;
    }

    splitIntoLetters(host, AUTH_LETTER_BASE_DELAY, AUTH_LETTER_STAGGER);
    markSessionSeen(AUTH_SESSION_KEY);

    // Click-to-skip: any pointer, key or touch interaction anywhere on the
    // page jumps straight to the finished state at once. Never calls
    // preventDefault/stopPropagation -- the interaction that triggered the
    // skip (clicking a link, typing into the email field, tapping the
    // submit button) must still do whatever it was already going to do.
    // Capture phase so this fires even if some other handler stops
    // propagation before it reaches document in the bubble phase.
    var skipEvents = ['pointerdown', 'keydown', 'touchstart'];
    var onSkip = function () {
      finish();
      for (var j = 0; j < skipEvents.length; j++) {
        document.removeEventListener(skipEvents[j], onSkip, true);
      }
    };
    for (var k = 0; k < skipEvents.length; k++) {
      document.addEventListener(skipEvents[k], onSkip, true);
    }
  }

  initLanding();
  initAuthWordmark();
})();
