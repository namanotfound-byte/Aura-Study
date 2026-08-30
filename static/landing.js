/* AuraStudy landing page.
 * Progressive enhancement only: the page is fully usable (title + both buttons,
 * correctly focusable) with this script never running at all. What this file adds:
 *   1. Splits the wordmark into individual letters so landing.css can stagger them in.
 *   2. Once the reveal has played, sends an already-authenticated visitor to /app.
 * Timing constants below are kept in step with the animation-delay values baked
 * into landing.css's keyframes (tagline at 1.05s, buttons at 1.15s+0.45s = 1.6s).
 */
(function () {
  'use strict';

  var LETTER_BASE_DELAY = 0.05; // seconds, first letter's animation-delay
  var LETTER_STAGGER = 0.055;   // seconds added per subsequent letter
  var TOTAL_ANIMATION_MS = 1700; // wordmark + tagline + buttons fully settled, plus a beat
  var REDUCED_MOTION_NAV_DELAY_MS = 250; // just enough for the finished title to register

  var body = document.body;
  var authenticated = body.getAttribute('data-authenticated') === 'true';

  var prefersReducedMotion = !!(
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );

  function goToApp() {
    window.location.href = '/app';
  }

  function splitLetters() {
    var host = document.getElementById('letters');
    if (!host) return;

    var text = host.textContent.trim();
    var frag = document.createDocumentFragment();

    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      var span = document.createElement('span');
      span.className = 'letter' + (ch === ' ' ? ' is-space' : '');
      span.style.setProperty('--delay', (LETTER_BASE_DELAY + i * LETTER_STAGGER).toFixed(3) + 's');
      span.textContent = ch === ' ' ? ' ' : ch;
      frag.appendChild(span);
    }

    host.textContent = '';
    host.appendChild(frag);
    host.classList.add('is-split');
  }

  if (prefersReducedMotion) {
    // landing.css's prefers-reduced-motion block already forces the title, tagline and
    // buttons to their finished state with no animation — nothing to split or stagger.
    if (authenticated) {
      window.setTimeout(goToApp, REDUCED_MOTION_NAV_DELAY_MS);
    }
    return;
  }

  splitLetters();

  if (authenticated) {
    window.setTimeout(goToApp, TOTAL_ANIMATION_MS);
  }
})();
