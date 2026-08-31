/*!
 * static/pip.js — AuraStudy floating timer popup ("Focus mode")
 *
 * Exposes a single global: window.AuraFocus
 * Plain ES2018 browser JS, no build step, no module system. Mirrors the
 * self-installing-wrap house style of static/sync.js and the
 * render-your-own-panel style of static/spotify.js.
 *
 * THE CORE IDEA: there is exactly ONE timer of record — the `let` variables
 * declared at the top of the inline app <script> (`isEngineActivelyRunning`,
 * `engineAnchorMs`, `bankedElapsedSeconds`, `countdownSecondsRemainingRegister`,
 * `countdownTotalSeconds`, `runningAccumulatedSeconds`) and the functions that
 * mutate them (`toggleEngineExecutionLoop`, `resetEngineDisplayState`,
 * `saveEngineWorkspaceBlockData`, `changeEngineMode`, `updateEngineDisplayString`).
 * Because this file is loaded via a classic <script src> tag AFTER that
 * inline script, on the same page, it shares the same realm's global lexical
 * environment — so those top-level `let` bindings and function declarations
 * are directly readable/callable here as bare identifiers, exactly like a
 * second <script> block would see them. This file never recomputes elapsed
 * time itself and never runs its own setInterval; every repaint of the
 * floating window, the tab title and the notification body happens from
 * inside a WRAPPED `updateEngineDisplayString()` — the app's own existing
 * tick already calls that function every 250ms while running, so the
 * floating window is strictly a second *view* onto the one real timer and
 * the two can never drift apart.
 *
 * PHASE 4 CHANGE (spec §1): the floating window must NOT appear while the
 * Timer view itself is on screen -- only once the user navigates to a
 * *different* screen, or the tab is hidden. It used to open on the Start
 * gesture; it now opens on the `switchView()` gesture that carries the user
 * AWAY from `view-timer` (still a click, so transient activation is live and
 * `requestWindow()` is still permitted synchronously inside that handler),
 * and it closes again the moment `switchView()` brings them BACK to
 * `view-timer`. The Start/Resume click itself no longer opens anything.
 *
 * WHAT THIS FILE DOES ON ITS OWN THE INSTANT IT LOADS (no wiring needed):
 *   - Self-installing wraps (same pattern as sync.js's wrap of
 *     saveStateToLocalStorageRegister) around:
 *       switchView                  -> leaving `view-timer` while a session
 *                                      is actively running opens the floating
 *                                      window (if the "float timer"
 *                                      preference is on); returning TO
 *                                      `view-timer` closes it. This is the
 *                                      primary open path -- see PHASE 4
 *                                      CHANGE above.
 *       toggleEngineExecutionLoop   -> no longer opens the floating window
 *                                      (Phase 4); still drives the wake lock.
 *       resetEngineDisplayState     -> ends the session -> closes the window,
 *                                      restores the tab title, clears any
 *                                      notification, releases the wake lock.
 *       saveEngineWorkspaceBlockData-> session complete/logged -> floating
 *                                      window shows a completion state, then
 *                                      closes itself after a few seconds.
 *       changeEngineMode            -> switching Countdown/Stopwatch also
 *                                      stops the run, so it gets the same
 *                                      end-of-session cleanup.
 *       updateEngineDisplayString   -> THE tick hook. Repaints the floating
 *                                      window / tab title / (lazily) the
 *                                      video-PiP canvas frame every time the
 *                                      app itself refreshes #timer-display.
 *   - Injects the small CSS needed for the Settings "Focus mode" toggles.
 *
 * WHAT index.html MUST WIRE UP (all done by Agent F already):
 *   1. <script src="/static/pip.js"></script> after sync.js and spotify.js.
 *   2. Once, in the same DOMContentLoaded chain as AuraSpotify.init() (i.e.
 *      AFTER loadStateFromLocalStorageRegister() has populated `appState`):
 *          if (typeof AuraFocus !== 'undefined') AuraFocus.init();
 *      This is where preference defaults get backfilled onto
 *      appState.profile.focusMode and the Settings toggles get their initial
 *      checked state — it cannot happen at load time because `appState`
 *      isn't hydrated from localStorage/server yet when this script parses.
 *   3. A static "Focus mode" card in #view-settings with four checkboxes
 *      (ids: focus-toggle-float / focus-toggle-notify / focus-toggle-wakelock /
 *      focus-toggle-sound, each `data-pref="floatTimer|notify|keepAwake|completionSound"`,
 *      onchange="AuraFocus.onPreferenceToggle(this)") and a
 *      `<p id="focus-mode-capability-note">` this file fills in with what the
 *      current browser actually supports.
 *
 *   There is no manual "Pop out timer" trigger anymore -- #view-timer's
 *   top-actions row now has a "Music" playback popover in that slot instead
 *   (static/spotify.js). The floating window still opens automatically per
 *   the DEGRADE CHAIN below; only the on-demand button is gone.
 *

 * DEGRADE CHAIN (see SPEC-PHASE2.md Part A, amended by SPEC-PHASE4.md §1):
 *   1. documentPictureInPicture.requestWindow() on the `switchView()` click
 *      that navigates AWAY from `view-timer` (Chromium only). Never on the
 *      Start/Resume gesture, and never while `view-timer` is the active panel.
 *   2. Best-effort documentPictureInPicture attempt on visibilitychange-hidden,
 *      wrapped in try/catch — expected to throw (no activation) almost every
 *      time; failure is silent.
 *   3. Web Notification + a live "⏳ MM:SS · AuraStudy" document.title while
 *      hidden, restored on refocus/session end.
 *   4. Where documentPictureInPicture doesn't exist but
 *      HTMLVideoElement.requestPictureInPicture does (Safari/Firefox): an
 *      offscreen <canvas>, redrawn from the same tick, piped through
 *      canvas.captureStream(1) into a muted/playsinline/autoplay <video>,
 *      and requestPictureInPicture() on THAT video from the gesture.
 *      NOTE: this branch cannot be exercised in a Chromium browser that
 *      supports documentPictureInPicture (Chromium always prefers path 1),
 *      so it has only been verified by code review — see Agent F's report.
 */
(function () {
  "use strict";

  // -- capability detection ------------------------------------------------

  var CAP = {
    documentPiP: !!(
      window.documentPictureInPicture &&
      typeof window.documentPictureInPicture.requestWindow === "function"
    ),
    videoPiP: (function () {
      try {
        var v = document.createElement("video");
        return (
          typeof v.requestPictureInPicture === "function" &&
          document.pictureInPictureEnabled !== false
        );
      } catch (e) {
        return false;
      }
    })(),
    notifications: "Notification" in window,
    wakeLock: !!(navigator.wakeLock && typeof navigator.wakeLock.request === "function"),
  };

  var DOC_TITLE_ORIGINAL = document.title;
  var RING_RADIUS = 52;
  var RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

  var DEFAULT_PREFS = { floatTimer: true, notify: true, keepAwake: true, completionSound: true };

  var STATE = {
    inited: false,
    pipMode: null, // 'document' | 'video' | null
    pipWindow: null, // the documentPictureInPicture window
    pipEls: null, // cached element refs inside the pip document
    pipRequestPending: false,
    videoEl: null,
    canvasEl: null,
    canvasCtx: null,
    completing: false,
    titleFallbackActive: false,
    activeNotification: null,
    permissionRequestInFlight: false,
    wakeLockSentinel: null,
    // True only while control is synchronously inside the wrapped
    // engineTickHandler's call to the ORIGINAL handler -- i.e. exactly the
    // window during which a countdown reaching zero on its own would call
    // saveEngineWorkspaceBlockData(). See the completion-notification wrap
    // below: this is how it tells a genuine auto-completion apart from the
    // manual "Log Session" button, which calls the same function directly.
    insideAutoTick: false,
    audioCtx: null,
  };

  // -- small helpers ---------------------------------------------------

  function prefs() {
    return appState.profile.focusMode;
  }

  function ensureProfileDefaults() {
    if (!appState.profile || typeof appState.profile !== "object") appState.profile = {};
    var fm = appState.profile.focusMode;
    if (!fm || typeof fm !== "object") {
      appState.profile.focusMode = Object.assign({}, DEFAULT_PREFS);
      return true;
    }
    var changed = false;
    for (var k in DEFAULT_PREFS) {
      if (!(k in fm)) {
        fm[k] = DEFAULT_PREFS[k];
        changed = true;
      }
    }
    return changed;
  }

  function toast(title, desc, good) {
    if (typeof triggerAlertToast === "function") triggerAlertToast(title, desc, good !== false);
  }

  function currentDisplayText() {
    var el = document.getElementById("timer-display");
    return el ? el.innerText : "00:00";
  }

  function pipSubtitleText() {
    var course = (appState.selectedCourse || "").trim();
    var modeLabel = appState.selectedMode === "countdown" ? "Countdown Block" : "Continuous Stopwatch";
    return course ? course + " · " + modeLabel : modeLabel;
  }

  function computeProgressFraction() {
    if (appState.selectedMode === "countdown") {
      if (!countdownTotalSeconds) return 0;
      return Math.max(0, Math.min(1, (countdownTotalSeconds - countdownSecondsRemainingRegister) / countdownTotalSeconds));
    }
    var cycle = 3600; // stopwatch has no natural total, so cycle the ring hourly
    return Math.max(0, Math.min(1, (runningAccumulatedSeconds % cycle) / cycle));
  }

  function currentAmbientVar() {
    return "var(--ambient-grad-" + (appState.ambientKey || "cottoncandy") + ")";
  }

  // -- Web Notification fallback (path 3) -------------------------------

  function buildNotificationBody() {
    var timeText = currentDisplayText();
    var course = appState.selectedCourse || "your session";
    return appState.selectedMode === "countdown"
      ? timeText + " left on " + course
      : timeText + " elapsed on " + course;
  }

  function requestNotificationPermissionIfNeeded() {
    if (!CAP.notifications) return;
    if (Notification.permission !== "default") return;
    if (STATE.permissionRequestInFlight) return;
    STATE.permissionRequestInFlight = true;
    toast(
      "Enable Notifications? 🔔",
      "AuraStudy would like to notify you when your timer is still running in the background."
    );
    Notification.requestPermission()
      .then(function (perm) {
        STATE.permissionRequestInFlight = false;
        if (perm !== "granted") {
          toast("Notifications Off", "No worries — the tab title will still show your countdown.", false);
        }
        renderCapabilityNote();
      })
      .catch(function () {
        STATE.permissionRequestInFlight = false;
      });
  }

  function maybeNotifyBackgroundRunning() {
    if (!prefs().notify || !CAP.notifications) return;
    if (Notification.permission === "default") {
      // Ask right now, at the moment the fallback is actually needed, rather
      // than nagging on page load. This notification round is skipped; the
      // next hidden cycle notifies once the user has answered.
      requestNotificationPermissionIfNeeded();
      return;
    }
    if (Notification.permission !== "granted") return;
    if (STATE.activeNotification) return;
    try {
      var n = new Notification("AuraStudy", {
        body: buildNotificationBody(),
        tag: "aurastudy-timer",
        silent: true,
      });
      n.onclick = function () {
        try {
          window.focus();
        } catch (e) {}
        try {
          n.close();
        } catch (e) {}
      };
      STATE.activeNotification = n;
    } catch (e) {
      // Notification constructor can throw on some mobile browsers even when
      // permission is granted (e.g. Android Chrome wants ServiceWorker
      // notifications instead) -- fail silently, title fallback still works.
    }
  }

  function clearActiveNotification() {
    if (STATE.activeNotification) {
      try {
        STATE.activeNotification.close();
      } catch (e) {}
      STATE.activeNotification = null;
    }
  }

  // -- genuine completion notification + sound ----------------------------
  //
  // Fires once per genuine countdown completion -- see STATE.insideAutoTick,
  // set only while the wrapped engineTickHandler is inside its call to the
  // ORIGINAL handler (i.e. exactly the moment a countdown reaching zero on
  // its own calls saveEngineWorkspaceBlockData()). Never for the manual "Log
  // Session" button, a pause, or a reset -- those call the same function
  // through a different path, with insideAutoTick false.

  function buildCompletionNotificationBody(session) {
    var mins = Math.max(1, Math.round(session.durationSeconds / 60));
    var minsLabel = mins + (mins === 1 ? " minute" : " minutes") + " logged";
    return session.course ? minsLabel + " for " + session.course + "." : minsLabel + ".";
  }

  // Reuses the SAME Notification permission state as the background-running
  // fallback above -- never calls Notification.requestPermission() itself.
  // A user who's never been asked (or said no) just gets the toast that
  // saveEngineWorkspaceBlockData() already shows; no second permission prompt.
  function notifyGenuineSessionCompletion(session) {
    playCompletionChime();
    if (!prefs().notify || !CAP.notifications) return;
    if (Notification.permission !== "granted") return;
    clearActiveNotification();
    try {
      var n = new Notification("AuraStudy", {
        body: "Session complete — " + buildCompletionNotificationBody(session),
        tag: "aurastudy-timer-complete",
        silent: true,
      });
      n.onclick = function () {
        try {
          window.focus();
        } catch (e) {}
        if (typeof switchView === "function") {
          try {
            switchView("timer", document.getElementById("nav-item-timer-toggle"));
          } catch (e) {}
        }
        try {
          n.close();
        } catch (e) {}
      };
      STATE.activeNotification = n;
    } catch (e) {
      // Same mobile-browser guard as maybeNotifyBackgroundRunning() above --
      // fail silently, the toast already covers it.
    }
  }

  // -- completion sound (Web Audio API only -- no audio file/external asset) --

  function primeAudioContext() {
    // Browsers gate audio playback behind a user gesture. Create (or resume)
    // the AudioContext here, synchronously inside the Start/Resume click
    // handler, so it's already running by the time a countdown naturally
    // reaches zero -- possibly minutes later, with no fresh gesture available.
    if (!prefs().completionSound) return;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!STATE.audioCtx) {
      try {
        STATE.audioCtx = new Ctx();
      } catch (e) {
        return;
      }
    }
    if (STATE.audioCtx.state === "suspended") {
      STATE.audioCtx.resume().catch(function () {});
    }
  }

  function playCompletionChime() {
    if (!prefs().completionSound) return; // must never play with the preference off
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!STATE.audioCtx && Ctx) {
      try {
        STATE.audioCtx = new Ctx();
      } catch (e) {
        return;
      }
    }
    var ctx = STATE.audioCtx;
    if (!ctx) return;
    if (ctx.state === "suspended") ctx.resume().catch(function () {});

    // Short, gentle two-note chime (a soft major sixth, C6 -> E6) -- sine
    // tones with a fast fade-in and an exponential fade-out so neither note
    // clicks or startles. No external asset; synthesised entirely here.
    try {
      var now = ctx.currentTime;
      [
        { freq: 1046.5, start: 0, dur: 0.32 },
        { freq: 1318.5, start: 0.16, dur: 0.42 },
      ].forEach(function (note) {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = note.freq;
        var startAt = now + note.start;
        var endAt = startAt + note.dur;
        gain.gain.setValueAtTime(0.0001, startAt);
        gain.gain.linearRampToValueAtTime(0.16, startAt + 0.04);
        gain.gain.exponentialRampToValueAtTime(0.0001, endAt);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(startAt);
        osc.stop(endAt + 0.02);
      });
    } catch (e) {
      /* Web Audio can throw in odd embedded contexts -- never break the app over a chime. */
    }
  }

  // -- document.title live countdown (path 3) ----------------------------

  function updateTitleFallback() {
    var shouldShow = document.hidden && isEngineActivelyRunning && !STATE.pipMode && prefs().notify;
    if (shouldShow) {
      STATE.titleFallbackActive = true;
      document.title = "⏳ " + currentDisplayText() + " · AuraStudy";
    } else {
      clearTitleFallback();
    }
  }

  function clearTitleFallback() {
    if (STATE.titleFallbackActive) {
      document.title = DOC_TITLE_ORIGINAL;
      STATE.titleFallbackActive = false;
    }
  }

  // -- Screen Wake Lock ---------------------------------------------------

  function requestWakeLock() {
    if (!CAP.wakeLock || !prefs().keepAwake || STATE.wakeLockSentinel) return;
    navigator.wakeLock
      .request("screen")
      .then(function (sentinel) {
        STATE.wakeLockSentinel = sentinel;
        sentinel.addEventListener("release", function () {
          if (STATE.wakeLockSentinel === sentinel) STATE.wakeLockSentinel = null;
        });
      })
      .catch(function () {
        /* denied, unsupported, or low battery -- fail silently per spec */
      });
  }

  function releaseWakeLock() {
    if (STATE.wakeLockSentinel) {
      try {
        STATE.wakeLockSentinel.release();
      } catch (e) {}
      STATE.wakeLockSentinel = null;
    }
  }

  function reacquireWakeLockIfNeeded() {
    if (isEngineActivelyRunning) requestWakeLock();
  }

  // -- stylesheet cloning into the Document PiP window ---------------------

  function cloneStylesInto(doc) {
    try {
      document.querySelectorAll("style").forEach(function (styleTag) {
        doc.head.appendChild(styleTag.cloneNode(true));
      });
    } catch (e) {
      /* ignore, the app's own inline styles are the important ones */
    }
    try {
      Array.prototype.forEach.call(document.styleSheets, function (sheet) {
        // Same-origin inline <style> blocks were already cloned verbatim
        // above (cheaper + preserves ordering); this pass only picks up
        // anything with an actual href (e.g. a future external stylesheet),
        // guarding the cross-origin case per spec.
        if (!sheet.href) return;
        try {
          var rules = sheet.cssRules;
          var cssText = Array.prototype.map
            .call(rules, function (r) {
              return r.cssText;
            })
            .join("\n");
          var styleEl = doc.createElement("style");
          styleEl.textContent = cssText;
          doc.head.appendChild(styleEl);
        } catch (e) {
          // Cross-origin stylesheet -- .cssRules throws a SecurityError.
          // Nothing we can do; skip it silently.
        }
      });
    } catch (e) {
      /* ignore */
    }
    injectPipContentStyles(doc);
  }

  function injectPipContentStyles(doc) {
    var style = doc.createElement("style");
    style.id = "af-pip-content-styles";
    style.textContent = [
      "html,body{height:100%;margin:0;}",
      "body.af-pip-body{display:flex !important;align-items:center;justify-content:center;padding:14px;overflow:hidden;}",
      ".af-wrap{width:100%;max-width:300px;background:rgba(255,255,255,0.78);backdrop-filter:blur(10px);" +
        "border:1px solid rgba(255,255,255,0.9);border-radius:20px;padding:18px 16px;display:flex;" +
        "flex-direction:column;align-items:center;gap:4px;box-shadow:0 10px 30px rgba(0,0,0,0.1);position:relative;}",
      ".af-course{font-size:13px;font-weight:800;color:var(--text-main);text-align:center;}",
      ".af-ring-wrap{position:relative;width:120px;height:120px;margin:8px 0 4px;}",
      ".af-ring-svg{width:100%;height:100%;transform:rotate(-90deg);}",
      ".af-ring-bg{fill:none;stroke:var(--border-color);stroke-width:8;}",
      ".af-ring-fg{fill:none;stroke:var(--neon-pink);stroke-width:8;stroke-linecap:round;transition:stroke-dashoffset .25s linear;}",
      ".af-time{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;" +
        "font-size:25px;font-weight:900;font-variant-numeric:tabular-nums;color:var(--text-main);}",
      ".af-mode{font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px;}",
      ".af-controls{display:flex;gap:10px;width:100%;}",
      ".af-btn{flex:1;border:2px solid var(--border-color);background:#fff;color:var(--neon-pink);" +
        "font-weight:700;font-size:13px;padding:9px 0;border-radius:12px;cursor:pointer;font-family:inherit;}",
      ".af-btn:hover{border-color:var(--neon-pink);}",
      ".af-btn-primary{background:var(--neon-pink);border-color:var(--neon-pink);color:#fff;}",
      ".af-complete{display:none;position:absolute;inset:0;background:rgba(255,255,255,0.96);border-radius:20px;" +
        "flex-direction:column;align-items:center;justify-content:center;gap:8px;text-align:center;padding:16px;}",
      ".af-wrap.af-complete-active .af-complete{display:flex;}",
      ".af-complete-emoji{font-size:32px;}",
      ".af-complete-text{font-size:13px;font-weight:800;color:var(--text-main);}",
    ].join("\n");
    doc.head.appendChild(style);
  }

  // -- Document Picture-in-Picture path (primary) --------------------------

  function openDocumentPip(trigger) {
    if (STATE.pipRequestPending) return true;
    STATE.pipRequestPending = true;
    var pipPromise;
    try {
      pipPromise = window.documentPictureInPicture.requestWindow({ width: 300, height: 280 });
    } catch (e) {
      STATE.pipRequestPending = false;
      if (trigger !== "tabswitch") console.warn("AuraFocus: could not open the floating timer window", e);
      return false;
    }
    pipPromise
      .then(function (pipWin) {
        STATE.pipRequestPending = false;
        setupDocumentPipWindow(pipWin);
      })
      .catch(function (e) {
        STATE.pipRequestPending = false;
        // The expected outcome for the tab-switch best-effort path (no
        // transient activation there) -- never surface this to the user.
        if (trigger !== "tabswitch") console.warn("AuraFocus: floating timer window request failed", e);
      });
    return true;
  }

  function setupDocumentPipWindow(pipWin) {
    STATE.pipWindow = pipWin;
    STATE.pipMode = "document";
    cloneStylesInto(pipWin.document);
    buildPipDom(pipWin.document);
    wirePipEvents();
    pipWin.document.title = "AuraStudy Timer";
    pipWin.addEventListener("pagehide", onPipWindowClosed, { once: true });
    clearTitleFallback();
    clearActiveNotification();
    onTick();
  }

  function onPipWindowClosed() {
    STATE.pipWindow = null;
    STATE.pipMode = null;
    STATE.pipEls = null;
  }

  function buildPipDom(doc) {
    var body = doc.body;
    body.innerHTML = "";
    body.className = "af-pip-body";
    syncPipTheme(doc);
    body.style.background = currentAmbientVar();

    var wrap = doc.createElement("div");
    wrap.className = "af-wrap";
    wrap.innerHTML =
      '<div class="af-course" id="af-course"></div>' +
      '<div class="af-ring-wrap">' +
      '<svg viewBox="0 0 120 120" class="af-ring-svg">' +
      '<circle class="af-ring-bg" cx="60" cy="60" r="' +
      RING_RADIUS +
      '"></circle>' +
      '<circle class="af-ring-fg" id="af-ring-fg" cx="60" cy="60" r="' +
      RING_RADIUS +
      '"></circle>' +
      "</svg>" +
      '<div class="af-time" id="af-time">00:00</div>' +
      "</div>" +
      '<div class="af-mode" id="af-mode"></div>' +
      '<div class="af-controls">' +
      '<button class="af-btn" id="af-reset-btn" type="button">Reset</button>' +
      '<button class="af-btn af-btn-primary" id="af-toggle-btn" type="button">Pause</button>' +
      "</div>" +
      '<div class="af-complete" id="af-complete">' +
      '<div class="af-complete-emoji">✨</div>' +
      '<div class="af-complete-text" id="af-complete-text">Session logged!</div>' +
      "</div>";
    body.appendChild(wrap);

    STATE.pipEls = {
      wrap: wrap,
      course: doc.getElementById("af-course"),
      mode: doc.getElementById("af-mode"),
      time: doc.getElementById("af-time"),
      ringFg: doc.getElementById("af-ring-fg"),
      resetBtn: doc.getElementById("af-reset-btn"),
      toggleBtn: doc.getElementById("af-toggle-btn"),
      completeText: doc.getElementById("af-complete-text"),
    };
  }

  // resetEngineDisplayState() in index.html shows a window.confirm() when
  // there's a minute or more on the clock -- but that dialog is spawned on
  // the MAIN window's `window` object. The floating window is a separate
  // top-level browsing context (Document Picture-in-Picture), typically
  // sitting ON TOP of the main window precisely because the user has
  // switched away from it, so the main window's confirm() either appears
  // behind the floating window (invisible, easy to miss entirely) or steals
  // focus in a confusing way. Either way, clicking Reset in the floating
  // window can look like it did nothing.
  //
  // Fix: do the same "is there something to lose" check here, and if so,
  // show the confirm INSIDE the floating window (STATE.pipWindow.confirm),
  // where the user is actually looking. Only call resetEngineDisplayState()
  // with skipConfirm once we already have an answer, so the main window
  // never shows a second, redundant (and possibly hidden) prompt. If nothing
  // is at stake, or the floating window can't produce its own dialog for some
  // reason, fall through to the normal call -- never silently discard time
  // that was never confirmed away.
  function handlePipResetClick() {
    var elapsedOnClock = Math.floor(runningAccumulatedSeconds || 0);
    if (elapsedOnClock >= 60 && STATE.pipWindow) {
      var mins = Math.floor(elapsedOnClock / 60);
      var confirmFn = null;
      try {
        if (typeof STATE.pipWindow.confirm === "function") confirmFn = STATE.pipWindow.confirm;
      } catch (e) {
        confirmFn = null;
      }
      if (confirmFn) {
        var ok;
        try {
          ok = confirmFn.call(
            STATE.pipWindow,
            "Reset the timer and discard " + mins + " minute" + (mins === 1 ? "" : "s") +
              " already on the clock?\n\nTo keep the time instead, cancel and choose Log Focus in the main AuraStudy window."
          );
        } catch (e) {
          // Some Document PiP implementations may not support confirm() in
          // the floating window -- fall back to the main-window path below
          // rather than assume an answer either way.
          confirmFn = null;
        }
        if (confirmFn) {
          if (!ok) return; // user cancelled inside the floating window -- nothing discarded
          resetEngineDisplayState(true); // already confirmed here; skip the (possibly hidden) main-window prompt
          return;
        }
      }
    }
    // Nothing at stake yet, or no floating-window dialog surface available --
    // the normal path (main-window confirm when there's something to lose,
    // silent when there isn't) is still safe, just not guaranteed visible.
    resetEngineDisplayState();
  }

  function wirePipEvents() {
    var els = STATE.pipEls;
    if (!els) return;
    // These call the bare global identifiers, which by the time any pip
    // window can possibly be open have already been replaced by this file's
    // own wraps below -- so a click in the floating window drives the exact
    // same start/pause/reset path a click in the main page would.
    els.resetBtn.addEventListener("click", handlePipResetClick);
    els.toggleBtn.addEventListener("click", function () {
      toggleEngineExecutionLoop();
    });
  }

  function syncPipTheme(doc) {
    var mainTheme = document.body.getAttribute("data-theme");
    if (mainTheme) doc.body.setAttribute("data-theme", mainTheme);
    else doc.body.removeAttribute("data-theme");
  }

  function paintDocumentPip() {
    var els = STATE.pipEls;
    if (!els || !STATE.pipWindow) return;
    syncPipTheme(STATE.pipWindow.document);
    STATE.pipWindow.document.body.style.background = currentAmbientVar();

    els.course.textContent = appState.selectedCourse || "";
    els.mode.textContent = appState.selectedMode === "countdown" ? "Countdown Block" : "Continuous Stopwatch";
    els.time.textContent = currentDisplayText();

    var frac = computeProgressFraction();
    els.ringFg.style.strokeDasharray = String(RING_CIRCUMFERENCE);
    els.ringFg.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - frac));

    els.toggleBtn.textContent = isEngineActivelyRunning ? "Pause" : runningAccumulatedSeconds > 0 ? "Resume" : "Start";
    els.wrap.classList.remove("af-complete-active");
  }

  function paintDocumentPipCompletion(logged) {
    var els = STATE.pipEls;
    if (!els) return;
    if (!logged) return;
    els.completeText.textContent = "Nice work! Logged into " + (appState.selectedCourse || "your course") + " ✨";
    els.wrap.classList.add("af-complete-active");
  }

  // -- video Picture-in-Picture path (Safari / Firefox fallback) -----------
  // NOTE: untested in this environment -- see the file header + Agent F's
  // report. Implemented conservatively from the documented API contract:
  // a <video> playing a captureStream() of a <canvas> we redraw ourselves.

  function ensureVideoPipElements() {
    if (STATE.videoEl) return;
    var canvas = document.createElement("canvas");
    canvas.width = 300;
    canvas.height = 220;
    var video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.autoplay = true;
    video.style.cssText = "position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;opacity:0;pointer-events:none;";
    document.body.appendChild(video);
    STATE.canvasEl = canvas;
    STATE.canvasCtx = canvas.getContext("2d");
    STATE.videoEl = video;
  }

  function openVideoPip(trigger) {
    try {
      ensureVideoPipElements();
      paintVideoCanvasFrame();
      if (!STATE.videoEl.srcObject) {
        STATE.videoEl.srcObject = STATE.canvasEl.captureStream(1);
      }
      var playResult = STATE.videoEl.play();
      if (playResult && typeof playResult.catch === "function") playResult.catch(function () {});
      var pipPromise = STATE.videoEl.requestPictureInPicture();
    } catch (e) {
      if (trigger !== "tabswitch") console.warn("AuraFocus: video picture-in-picture failed", e);
      return false;
    }
    pipPromise
      .then(function () {
        STATE.pipMode = "video";
        clearTitleFallback();
        clearActiveNotification();
        STATE.videoEl.addEventListener("leavepictureinpicture", onVideoPipClosed, { once: true });
        onTick();
      })
      .catch(function (e) {
        if (trigger !== "tabswitch") console.warn("AuraFocus: video picture-in-picture request rejected", e);
      });
    return true;
  }

  function onVideoPipClosed() {
    STATE.pipMode = null;
    if (STATE.videoEl) {
      try {
        STATE.videoEl.pause();
      } catch (e) {}
    }
  }

  function paintVideoCanvasFrame() {
    if (!STATE.canvasCtx) return;
    var ctx = STATE.canvasCtx;
    var W = STATE.canvasEl.width;
    var H = STATE.canvasEl.height;
    var bg = readThemeToken("--bg-main", "#FFF5F8");
    var card = readThemeToken("--bg-card", "#FFFFFF");
    var pink = readThemeToken("--neon-pink", "#FF66B2");
    var border = readThemeToken("--border-color", "#FFD3E3");
    var textColor = readThemeToken("--text-main", "#4A3E43");
    var mutedColor = readThemeToken("--text-muted", "#8A7680");

    ctx.clearRect(0, 0, W, H);
    var grad = ctx.createLinearGradient(0, 0, W, H);
    grad.addColorStop(0, bg);
    grad.addColorStop(1, card);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);

    var cx = W / 2;
    var cy = 86;
    var r = 54;
    ctx.lineWidth = 8;
    ctx.strokeStyle = border;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();

    var frac = computeProgressFraction();
    ctx.strokeStyle = pink;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2);
    ctx.stroke();

    ctx.fillStyle = textColor;
    ctx.textAlign = "center";
    ctx.font = "700 32px 'Segoe UI', Roboto, sans-serif";
    ctx.fillText(currentDisplayText(), cx, cy + 11);

    ctx.font = "700 14px 'Segoe UI', Roboto, sans-serif";
    ctx.fillText(pipSubtitleText(), cx, H - 32);

    ctx.fillStyle = mutedColor;
    ctx.font = "600 11px 'Segoe UI', Roboto, sans-serif";
    ctx.fillText("AuraStudy", cx, H - 12);
  }

  function paintVideoCanvasCompletion() {
    if (!STATE.canvasCtx) return;
    paintVideoCanvasFrame();
    var ctx = STATE.canvasCtx;
    var W = STATE.canvasEl.width;
    var H = STATE.canvasEl.height;
    ctx.fillStyle = "rgba(255,255,255,0.92)";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = readThemeToken("--text-main", "#4A3E43");
    ctx.textAlign = "center";
    ctx.font = "700 40px 'Segoe UI', Roboto, sans-serif";
    ctx.fillText("✨", W / 2, H / 2 - 6);
    ctx.font = "700 15px 'Segoe UI', Roboto, sans-serif";
    ctx.fillText("Session logged!", W / 2, H / 2 + 28);
  }

  // -- open/close dispatch --------------------------------------------

  function attemptOpenFloatingWindow(trigger) {
    if (STATE.pipMode === "document" && STATE.pipWindow) {
      try {
        STATE.pipWindow.focus();
      } catch (e) {}
      return true;
    }
    if (STATE.pipMode === "video") return true;
    if (CAP.documentPiP) return openDocumentPip(trigger);
    if (CAP.videoPiP) return openVideoPip(trigger);
    return false;
  }

  function closeFloatingWindow() {
    if (STATE.pipMode === "document" && STATE.pipWindow) {
      try {
        STATE.pipWindow.close();
      } catch (e) {
        onPipWindowClosed();
      }
    } else if (STATE.pipMode === "video") {
      try {
        if (document.pictureInPictureElement === STATE.videoEl) {
          document.exitPictureInPicture().catch(function () {});
        } else {
          onVideoPipClosed();
        }
      } catch (e) {
        onVideoPipClosed();
      }
    }
  }

  // -- the single tick hook (wraps updateEngineDisplayString) --------------

  function onTick() {
    if (STATE.pipMode === "document") {
      paintDocumentPip();
    } else if (STATE.pipMode === "video") {
      paintVideoCanvasFrame();
    }
    updateTitleFallback();
  }

  // Opens the floating window the moment the user navigates AWAY from the
  // Timer view while a session is actively running (Phase 4 §1). Called from
  // inside the switchView() wrap below, synchronously within that click
  // handler's call stack, so transient activation is still live.
  function maybeFloatOnLeavingTimer() {
    if (!prefs().floatTimer) return;
    if (!isEngineActivelyRunning) return;
    if (STATE.pipMode) return; // already open, nothing to do
    attemptOpenFloatingWindow("navaway");
  }

  function afterEngineStateChange() {
    if (isEngineActivelyRunning) {
      requestWakeLock();
      // Warm up (or resume) the AudioContext on this same Start/Resume
      // gesture so the completion chime -- fired with no fresh gesture of
      // its own, whenever the countdown naturally reaches zero later -- is
      // allowed to actually make sound under browser autoplay policies.
      primeAudioContext();
    } else {
      releaseWakeLock();
    }
  }

  function endSessionCleanup() {
    closeFloatingWindow();
    clearTitleFallback();
    clearActiveNotification();
    releaseWakeLock();
  }

  function finishSession(logged) {
    clearTitleFallback();
    clearActiveNotification();
    releaseWakeLock();
    if (STATE.pipMode === "document") {
      if (logged) paintDocumentPipCompletion(true);
      setTimeout(closeFloatingWindow, logged ? 3000 : 400);
    } else if (STATE.pipMode === "video") {
      if (logged) paintVideoCanvasCompletion();
      setTimeout(closeFloatingWindow, logged ? 3000 : 400);
    }
  }

  // -- Settings "Focus mode" card -------------------------------------

  function injectSettingsCardStyles() {
    if (document.getElementById("af-settings-styles")) return;
    var style = document.createElement("style");
    style.id = "af-settings-styles";
    style.textContent = [
      ".focus-mode-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 0;border-bottom:1px solid var(--border-color);}",
      ".focus-mode-row:last-of-type{border-bottom:none;}",
      ".focus-mode-row-label{font-size:13px;font-weight:700;color:var(--text-main);}",
      ".focus-mode-row-desc{font-size:11px;color:var(--text-muted);margin-top:2px;max-width:380px;line-height:1.5;}",
      ".focus-toggle-switch{position:relative;display:inline-block;width:44px;height:26px;flex-shrink:0;}",
      ".focus-toggle-switch input{position:absolute;opacity:0;width:100%;height:100%;margin:0;cursor:pointer;}",
      ".focus-toggle-slider{position:absolute;inset:0;background:var(--border-color);border-radius:999px;transition:background .2s;pointer-events:none;}",
      ".focus-toggle-slider::before{content:'';position:absolute;width:20px;height:20px;left:3px;top:3px;background:#fff;border-radius:50%;transition:transform .2s;box-shadow:0 2px 6px rgba(0,0,0,0.15);}",
      ".focus-toggle-switch input:checked + .focus-toggle-slider{background:var(--neon-pink);}",
      ".focus-toggle-switch input:checked + .focus-toggle-slider::before{transform:translateX(18px);}",
      ".focus-toggle-switch input:focus-visible + .focus-toggle-slider{outline:2px solid var(--neon-pink);outline-offset:2px;}",
      /* -- mobile: give the switch a real >=44px tap target and let long labels wrap without squashing it -- */
      "@media (max-width:480px){.focus-mode-row{gap:12px;}.focus-mode-row-desc{max-width:none;}}",
      "@media (pointer:coarse){.focus-toggle-switch{width:44px;height:44px;}.focus-toggle-slider{top:9px;bottom:9px;left:0;right:0;}}",
    ].join("\n");
    document.head.appendChild(style);
  }

  function renderFocusSettingsUI() {
    var p = prefs();
    var floatEl = document.getElementById("focus-toggle-float");
    var notifyEl = document.getElementById("focus-toggle-notify");
    var wakeEl = document.getElementById("focus-toggle-wakelock");
    var soundEl = document.getElementById("focus-toggle-sound");
    if (floatEl) floatEl.checked = !!p.floatTimer;
    if (notifyEl) notifyEl.checked = !!p.notify;
    if (wakeEl) wakeEl.checked = !!p.keepAwake;
    if (soundEl) soundEl.checked = !!p.completionSound;
    renderCapabilityNote();
  }

  function renderCapabilityNote() {
    var note = document.getElementById("focus-mode-capability-note");
    if (!note) return;
    var lines = [];
    if (CAP.documentPiP) {
      lines.push("Your browser supports floating always-on-top timer windows. ✨");
    } else if (CAP.videoPiP) {
      lines.push("Your browser doesn't support floating windows directly, so AuraStudy uses video picture-in-picture instead.");
    } else {
      lines.push("This browser can't float the timer window, so AuraStudy falls back to a browser notification and a live countdown in the tab title.");
    }
    if (CAP.notifications && Notification.permission === "denied") {
      lines.push("Notifications are blocked in your browser settings, so the tab-title countdown will be the only background indicator.");
    }
    if (!CAP.wakeLock) {
      lines.push("This browser doesn't support keeping the screen awake.");
    }
    note.textContent = lines.join(" ");
  }

  function onPreferenceToggle(el) {
    if (!el) return;
    var key = el.getAttribute("data-pref");
    if (!key || !(key in DEFAULT_PREFS)) return;
    prefs()[key] = !!el.checked;
    saveStateToLocalStorageRegister();

    if (key === "notify" && el.checked) requestNotificationPermissionIfNeeded();
    if (key === "keepAwake") {
      if (el.checked) reacquireWakeLockIfNeeded();
      else releaseWakeLock();
    }
    renderCapabilityNote();
  }

  // NOTE: the manual "Pop out timer" button (and the popOutTimer() function
  // that used to back it, triggered as attemptOpenFloatingWindow("manual"))
  // was removed from #view-timer's top-actions row -- that slot is now a
  // "Music" playback popover (see static/spotify.js). The automatic floating
  // behaviour above (opening on switchView() away from the Timer view, and
  // the best-effort tab-switch/notification/wake-lock paths) is untouched;
  // only the on-demand manual trigger is gone.

  // -- lifecycle listeners ---------------------------------------------

  function onVisibilityChange() {
    if (document.hidden) {
      if (isEngineActivelyRunning) {
        if (!STATE.pipMode && prefs().floatTimer) {
          try {
            attemptOpenFloatingWindow("tabswitch");
          } catch (e) {
            /* NotAllowedError -- no transient activation here, expected */
          }
        }
        if (!STATE.pipMode) {
          maybeNotifyBackgroundRunning();
          updateTitleFallback();
        }
      }
    } else {
      clearTitleFallback();
      clearActiveNotification();
      reacquireWakeLockIfNeeded();
    }
  }

  // -- wrapping the existing engine functions (self-installing) -----------

  function wrapGlobalFn(name, wrapperFactory) {
    var original = window[name];
    if (typeof original !== "function") {
      console.warn('AuraFocus: expected global function "' + name + '" was not found; the floating timer will not hook into it.');
      return;
    }
    window[name] = function () {
      return wrapperFactory(original, this, arguments);
    };
  }

  wrapGlobalFn("toggleEngineExecutionLoop", function (original, thisArg, args) {
    // Phase 4 §1: the Start/Resume gesture no longer opens the floating
    // window by itself -- it only opens once the user leaves the Timer view
    // (see the switchView() wrap below) or the tab is hidden (see
    // onVisibilityChange). This wrap now only drives the wake lock.
    var result = original.apply(thisArg, args);
    afterEngineStateChange();
    return result;
  });

  wrapGlobalFn("switchView", function (original, thisArg, args) {
    var targetPanelKey = args[0];
    var activePanelBefore = document.querySelector(".view-panel.active");
    var wasOnTimer = !!(activePanelBefore && activePanelBefore.id === "view-timer");

    var result = original.apply(thisArg, args);

    if (targetPanelKey === "timer") {
      // Back on the Timer screen -- the floating window must close, per spec.
      if (STATE.pipMode) closeFloatingWindow();
    } else if (wasOnTimer) {
      // Left the Timer screen via a click -- transient activation is live.
      maybeFloatOnLeavingTimer();
    }

    return result;
  });

  wrapGlobalFn("resetEngineDisplayState", function (original, thisArg, args) {
    var result = original.apply(thisArg, args);
    if (!STATE.completing) endSessionCleanup();
    return result;
  });

  // The tick's only call to saveEngineWorkspaceBlockData() is the genuine
  // "countdown reached zero on its own" branch (see engineTickHandler in
  // index.html) -- marking STATE.insideAutoTick for exactly the duration of
  // this call is how the wrap below tells that apart from the manual "Log
  // Session" button, which calls saveEngineWorkspaceBlockData() directly.
  wrapGlobalFn("engineTickHandler", function (original, thisArg, args) {
    STATE.insideAutoTick = true;
    try {
      return original.apply(thisArg, args);
    } finally {
      STATE.insideAutoTick = false;
    }
  });

  wrapGlobalFn("saveEngineWorkspaceBlockData", function (original, thisArg, args) {
    var wasGenuineCompletion = STATE.insideAutoTick;
    var beforeCount = appState.sessions ? appState.sessions.length : 0;
    STATE.completing = true;
    var result = original.apply(thisArg, args);
    STATE.completing = false;
    var logged = !!(appState.sessions && appState.sessions.length > beforeCount);
    finishSession(logged);
    // Exactly-once, genuine-completion-only notification + chime: never for
    // the manual "Log Session" button (wasGenuineCompletion is false there),
    // never for a pause or reset (neither calls this function at all), and
    // this function itself only ever runs once per completed session.
    if (wasGenuineCompletion && logged) {
      notifyGenuineSessionCompletion(appState.sessions[0]);
    }
    return result;
  });

  wrapGlobalFn("changeEngineMode", function (original, thisArg, args) {
    var result = original.apply(thisArg, args);
    endSessionCleanup();
    return result;
  });

  wrapGlobalFn("updateEngineDisplayString", function (original, thisArg, args) {
    var result = original.apply(thisArg, args);
    onTick();
    return result;
  });

  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("beforeunload", function () {
    closeFloatingWindow();
  });

  // -- public API --------------------------------------------------------

  function init() {
    if (STATE.inited) return;
    STATE.inited = true;
    var backfilled = ensureProfileDefaults();
    injectSettingsCardStyles();
    renderFocusSettingsUI();
    if (backfilled && typeof saveStateToLocalStorageRegister === "function") {
      saveStateToLocalStorageRegister();
    }
  }

  window.AuraFocus = {
    init: init,
    onPreferenceToggle: onPreferenceToggle,
  };
})();
