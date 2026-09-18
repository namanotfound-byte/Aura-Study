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
 *   3. Web Notification (via service worker) while hidden; document.title
 *      stays "AuraStudy" at all times.
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
    serviceWorker: "serviceWorker" in navigator,
    mediaSession: "mediaSession" in navigator,
    wakeLock: !!(navigator.wakeLock && typeof navigator.wakeLock.request === "function"),
  };

  var PIP_WINDOW_WIDTH = 196;
  var PIP_WINDOW_HEIGHT = 96;

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
    activeNotification: null,
    permissionRequestInFlight: false,
    swRegistration: null,
    swReady: false,
    persistentNotificationActive: false,
    wakeLockSentinel: null,
    // True only while control is synchronously inside the wrapped
    // engineTickHandler's call to the ORIGINAL handler -- i.e. exactly the
    // window during which a countdown reaching zero on its own would call
    // saveEngineWorkspaceBlockData(). See the completion-notification wrap
    // below: this is how it tells a genuine auto-completion apart from the
    // manual "Log Session" button, which calls the same function directly.
    insideAutoTick: false,
    audioCtx: null,
    // True while switchView() is opening PiP before/just after the heavy
    // original handler — blocks focus/visibility from closing a just-opened
    // window while #view-timer may still look active for a moment.
    suppressPipClose: false,
  };

  // -- small helpers ---------------------------------------------------

  function prefs() {
    ensureProfileDefaults();
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

  function isHourLongDisplay() {
    var raw =
      appState.selectedMode === "countdown" ||
      (typeof enginePhase !== "undefined" && enginePhase === "break")
        ? countdownSecondsRemainingRegister
        : runningAccumulatedSeconds;
    var total = typeof raw === "number" && isFinite(raw) && raw > 0 ? Math.floor(raw) : 0;
    return total >= 3600;
  }

  function pipSubtitleText() {
    if (typeof enginePhase !== "undefined" && enginePhase === "break") {
      return "Break";
    }
    var course = (appState.selectedCourse || "").trim();
    var modeLabel = appState.selectedMode === "countdown" ? "Countdown Block" : "Continuous Stopwatch";
    return course ? course + " · " + modeLabel : modeLabel;
  }

  function computeProgressFraction() {
    if (appState.selectedMode === "countdown" || (typeof enginePhase !== "undefined" && enginePhase === "break")) {
      if (!countdownTotalSeconds) return 0;
      return Math.max(0, Math.min(1, (countdownTotalSeconds - countdownSecondsRemainingRegister) / countdownTotalSeconds));
    }
    var cycle = 3600; // stopwatch has no natural total, so cycle the ring hourly
    return Math.max(0, Math.min(1, (runningAccumulatedSeconds % cycle) / cycle));
  }

  function currentAmbientVar() {
    var key = appState.ambientKey || "pink-cottoncandy";
    return "var(--timer-grad-" + key + ")";
  }

  // -- Web Notification fallback (path 3) -------------------------------

  function buildNotificationBody() {
    var timeText = currentDisplayText();
    if (typeof enginePhase !== "undefined" && enginePhase === "break") {
      return timeText + " left on your break";
    }
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

  function registerServiceWorker() {
    if (!CAP.serviceWorker) return Promise.resolve(null);
    return navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then(function (reg) {
        STATE.swRegistration = reg;
        STATE.swReady = true;
        return reg;
      })
      .catch(function () {
        STATE.swRegistration = null;
        STATE.swReady = false;
        return null;
      });
  }

  function buildTimerStatePayload() {
    return {
      isRunning: !!isEngineActivelyRunning,
      anchorMs: engineAnchorMs,
      bankedSeconds: wholeSeconds(bankedElapsedSeconds),
      mode: appState.selectedMode === "countdown" ? "countdown" : "stopwatch",
      course: appState.selectedCourse || "",
      countdownTotalSeconds: wholeSeconds(countdownTotalSeconds),
      phase: typeof enginePhase !== "undefined" && enginePhase === "break" ? "break" : "study",
    };
  }

  function isPageVisible() {
    return document.visibilityState === "visible";
  }

  function postTimerMessageToServiceWorker(type) {
    if (!STATE.swReady || !STATE.swRegistration) return Promise.resolve(false);
    if (!prefs().notify) return Promise.resolve(false);
    if (Notification.permission !== "granted") return Promise.resolve(false);
    if (type === "TIMER_SHOW" && isPageVisible()) return Promise.resolve(false);
    var payload = buildTimerStatePayload();
    payload.type = type;
    var target = STATE.swRegistration.active;
    if (!target && STATE.swRegistration.waiting) target = STATE.swRegistration.waiting;
    if (!target) {
      return navigator.serviceWorker.ready
        .then(function (reg) {
          if (reg.active) reg.active.postMessage(payload);
        })
        .catch(function () {});
    }
    try {
      target.postMessage(payload);
    } catch (e) {}
    return Promise.resolve(true);
  }

  function maybeShowLegacyNotification() {
    if (!prefs().notify || !CAP.notifications) return;
    if (Notification.permission !== "granted") return;
    if (STATE.activeNotification) {
      try {
        STATE.activeNotification.body = buildNotificationBody();
      } catch (e) {}
      return;
    }
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
      /* Android Chrome often requires SW notifications instead. */
    }
  }

  function syncPersistentSessionNotification() {
    if (!prefs().notify || !isEngineActivelyRunning) {
      if (!isEngineActivelyRunning) clearPersistentSessionNotification();
      return;
    }
    if (isPageVisible()) {
      updateMediaSession();
      return;
    }
    if (STATE.pipMode) {
      updateMediaSession();
      return;
    }
    if (Notification.permission === "default") {
      requestNotificationPermissionIfNeeded();
      return;
    }
    if (Notification.permission !== "granted") {
      updateMediaSession();
      return;
    }

    var type = STATE.persistentNotificationActive ? "TIMER_UPDATE" : "TIMER_SHOW";

    postTimerMessageToServiceWorker(type).then(function (posted) {
      if (posted) {
        STATE.persistentNotificationActive = true;
        clearActiveNotification();
      } else if (document.hidden) {
        maybeShowLegacyNotification();
      }
      updateMediaSession();
    });
  }

  function clearPersistentSessionNotification() {
    STATE.persistentNotificationActive = false;
    postTimerMessageToServiceWorker("TIMER_CLEAR");
    clearActiveNotification();
    clearMediaSession();
  }

  function resetNotificationDismissState() {
    if (!STATE.swReady || !STATE.swRegistration) return;
    var target = STATE.swRegistration.active;
    if (!target && STATE.swRegistration.waiting) target = STATE.swRegistration.waiting;
    if (!target) return;
    try {
      target.postMessage({ type: "TIMER_RESET_DISMISS" });
    } catch (e) {}
  }

  function onPageVisible() {
    clearPersistentSessionNotification();
    resetNotificationDismissState();
    closeFloatingWindow();
  }

  function showBackgroundControls() {
    if (!isEngineActivelyRunning || isPageVisible()) return;
    if (prefs().floatTimer && attemptOpenFloatingWindow("tabswitch")) return;
    syncPersistentSessionNotification();
  }

  function clearActiveNotification() {
    if (STATE.activeNotification) {
      try {
        STATE.activeNotification.close();
      } catch (e) {}
      STATE.activeNotification = null;
    }
  }

  function updateMediaSession() {
    if (!CAP.mediaSession || !prefs().notify || !isEngineActivelyRunning) {
      clearMediaSession();
      return;
    }
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: currentDisplayText(),
        artist: pipSubtitleText(),
        album: "AuraStudy",
        artwork: [
          {
            src: "/static/brand/aurastudy-icon-192.png",
            sizes: "192x192",
            type: "image/png",
          },
        ],
      });
      navigator.mediaSession.playbackState = "playing";
    } catch (e) {}
  }

  function clearMediaSession() {
    if (!CAP.mediaSession) return;
    try {
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.playbackState = "none";
    } catch (e) {}
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
      "body.af-pip-body{display:flex !important;align-items:center;justify-content:center;padding:8px;overflow:hidden;}",
      ".af-wrap{width:100%;max-width:180px;background:rgba(255,255,255,0.82);backdrop-filter:blur(10px);" +
        "border:1px solid rgba(255,255,255,0.92);border-radius:14px;padding:10px 12px;display:flex;" +
        "flex-direction:column;align-items:center;gap:8px;box-shadow:0 6px 18px rgba(0,0,0,0.1);}",
      ".af-time{font-size:22px;font-weight:900;font-variant-numeric:tabular-nums;color:var(--text-main);" +
        "white-space:nowrap;line-height:1;letter-spacing:-0.02em;}",
      ".af-time--hours{font-size:17px;}",
      ".af-controls{display:flex;gap:6px;width:100%;}",
      ".af-btn{flex:1;border:1.5px solid var(--border-color);background:#fff;color:var(--neon-pink);" +
        "font-weight:700;font-size:11px;padding:6px 0;border-radius:10px;cursor:pointer;font-family:inherit;}",
      ".af-btn:hover{border-color:var(--neon-pink);}",
      ".af-btn-primary{background:var(--neon-pink);border-color:var(--neon-pink);color:#fff;}",
    ].join("\n");
    doc.head.appendChild(style);
  }

  // -- Document Picture-in-Picture path (primary) --------------------------

  function openDocumentPip(trigger) {
    if (STATE.pipRequestPending) return true;
    STATE.pipRequestPending = true;
    var pipPromise;
    try {
      pipPromise = window.documentPictureInPicture.requestWindow({
        width: PIP_WINDOW_WIDTH,
        height: PIP_WINDOW_HEIGHT,
      });
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
    pipWin.document.title = "AuraStudy";
    pipWin.addEventListener("pagehide", onPipWindowClosed, { once: true });
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
      '<div class="af-time" id="af-time">00:00</div>' +
      '<div class="af-controls">' +
      '<button class="af-btn af-btn-primary" id="af-toggle-btn" type="button">Pause</button>' +
      '<button class="af-btn" id="af-log-btn" type="button">Log</button>' +
      "</div>";
    body.appendChild(wrap);

    STATE.pipEls = {
      wrap: wrap,
      time: doc.getElementById("af-time"),
      toggleBtn: doc.getElementById("af-toggle-btn"),
      logBtn: doc.getElementById("af-log-btn"),
    };
  }

  function wirePipEvents() {
    var els = STATE.pipEls;
    if (!els) return;
    els.toggleBtn.addEventListener("click", function () {
      toggleEngineExecutionLoop();
    });
    els.logBtn.addEventListener("click", function () {
      if (typeof saveEngineWorkspaceBlockData === "function") saveEngineWorkspaceBlockData();
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

    els.time.textContent = currentDisplayText();
    if (isHourLongDisplay()) {
      els.time.classList.add("af-time--hours");
    } else {
      els.time.classList.remove("af-time--hours");
    }

    els.toggleBtn.textContent = isEngineActivelyRunning ? "Pause" : runningAccumulatedSeconds > 0 ? "Resume" : "Start";
  }

  // -- video Picture-in-Picture path (Safari / Firefox fallback) -----------
  // NOTE: untested in this environment -- see the file header + Agent F's
  // report. Implemented conservatively from the documented API contract:
  // a <video> playing a captureStream() of a <canvas> we redraw ourselves.

  function ensureVideoPipElements() {
    if (STATE.videoEl) return;
    var canvas = document.createElement("canvas");
    canvas.width = PIP_WINDOW_WIDTH;
    canvas.height = PIP_WINDOW_HEIGHT;
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
    var textColor = readThemeToken("--text-main", "#4A3E43");

    ctx.clearRect(0, 0, W, H);
    var grad = ctx.createLinearGradient(0, 0, W, H);
    grad.addColorStop(0, bg);
    grad.addColorStop(1, card);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);

    ctx.fillStyle = textColor;
    ctx.textAlign = "center";
    ctx.font = isHourLongDisplay()
      ? "700 17px 'Segoe UI', Roboto, sans-serif"
      : "700 22px 'Segoe UI', Roboto, sans-serif";
    ctx.fillText(currentDisplayText(), W / 2, H * 0.42);
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
    if (isPageVisible()) {
      if (isEngineActivelyRunning && prefs().notify) updateMediaSession();
      return;
    }
    if (STATE.activeNotification) {
      try {
        STATE.activeNotification.body = buildNotificationBody();
      } catch (e) {}
    }
    if (isEngineActivelyRunning && prefs().notify) updateMediaSession();
  }

  // Opens the floating window the moment the user navigates AWAY from the
  // Timer view while a session is actively running (Phase 4 §1). Called from
  // inside the switchView() wrap below, synchronously within that click
  // handler's call stack, so transient activation is still live.
  function maybeFloatOnLeavingTimer() {
    if (!prefs().floatTimer) return;
    if (!isEngineActivelyRunning) return;
    if (isPageVisible()) return;
    if (STATE.pipMode) return;
    attemptOpenFloatingWindow("navaway");
  }

  function afterEngineStateChange() {
    if (isEngineActivelyRunning) {
      requestWakeLock();
      requestNotificationPermissionIfNeeded();
      if (document.hidden) {
        showBackgroundControls();
      } else {
        updateMediaSession();
      }
      // Warm up (or resume) the AudioContext on this same Start/Resume
      // gesture so the completion chime -- fired with no fresh gesture of
      // its own, whenever the countdown naturally reaches zero later -- is
      // allowed to actually make sound under browser autoplay policies.
      primeAudioContext();
    } else {
      clearPersistentSessionNotification();
      releaseWakeLock();
    }
  }

  function endSessionCleanup() {
    closeFloatingWindow();
    clearPersistentSessionNotification();
    releaseWakeLock();
  }

  function finishSession(logged) {
    clearPersistentSessionNotification();
    releaseWakeLock();
    if (STATE.pipMode === "document" || STATE.pipMode === "video") {
      setTimeout(closeFloatingWindow, logged ? 1200 : 400);
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
      lines.push("This browser can't float the timer window, so AuraStudy falls back to a persistent notification and a live countdown in the tab title.");
    }
    if (CAP.serviceWorker) {
      lines.push("Install AuraStudy as a home-screen app (Add to Home Screen) for the strongest lock-screen timer notification on Android Chrome.");
    }
    if (CAP.notifications && Notification.permission === "denied") {
      lines.push("Notifications are blocked in your browser settings, so the tab-title countdown will be the only background indicator.");
    }
    if (!CAP.wakeLock) {
      lines.push("This browser doesn't support keeping the screen awake.");
    }
    lines.push("iOS Safari has weaker background notification support than Android Chrome — timer time still accrues via wall-clock math when you return.");
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

  function isTimerViewActive() {
    var panel = document.getElementById("view-timer");
    return !!(panel && panel.classList.contains("active"));
  }

  function onVisibilityChange() {
    if (document.hidden) {
      if (isEngineActivelyRunning) showBackgroundControls();
    } else {
      onPageVisible();
      if (isEngineActivelyRunning) {
        reacquireWakeLockIfNeeded();
        updateMediaSession();
      }
    }
  }

  function handleServiceWorkerMessage(event) {
    var data = event.data || {};
    if (data.type === "AURASTUDY_PAUSE") {
      if (isEngineActivelyRunning && typeof toggleEngineExecutionLoop === "function") {
        toggleEngineExecutionLoop();
      }
      return;
    }
    if (data.type === "AURASTUDY_LOG") {
      if (typeof saveEngineWorkspaceBlockData === "function") saveEngineWorkspaceBlockData();
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
    var leavingTimer = wasOnTimer && targetPanelKey !== "timer";

    // Open PiP synchronously on the nav click *before* the heavy switchView
    // body runs (charts, lucide, tables) so transient activation is still live.
    if (leavingTimer) {
      STATE.suppressPipClose = true;
      maybeFloatOnLeavingTimer();
    }

    var result = original.apply(thisArg, args);

    if (targetPanelKey === "timer") {
      // Back on the Timer screen -- the floating window must close, per spec.
      STATE.suppressPipClose = false;
      if (STATE.pipMode) closeFloatingWindow();
    } else if (leavingTimer) {
      setTimeout(function () {
        STATE.suppressPipClose = false;
      }, 50);
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
    var wasRunning = isEngineActivelyRunning;
    var result = original.apply(thisArg, args);
    function afterModeChange() {
      // Async confirm can cancel the switch — only tear down PiP when the
      // mode change actually stopped a running session.
      if (wasRunning && !isEngineActivelyRunning) {
        endSessionCleanup();
      }
    }
    if (result && typeof result.then === "function") {
      return result.then(function (value) {
        afterModeChange();
        return value;
      });
    }
    afterModeChange();
    return result;
  });

  wrapGlobalFn("updateEngineDisplayString", function (original, thisArg, args) {
    var result = original.apply(thisArg, args);
    onTick();
    return result;
  });

  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("focus", function () {
    if (!document.hidden) onPageVisible();
  });
  window.addEventListener("pageshow", function () {
    if (!document.hidden) onPageVisible();
  });
  window.addEventListener("beforeunload", function () {
    closeFloatingWindow();
  });
  if (CAP.serviceWorker && navigator.serviceWorker.addEventListener) {
    navigator.serviceWorker.addEventListener("message", handleServiceWorkerMessage);
  }

  // -- public API --------------------------------------------------------

  function init() {
    if (STATE.inited) return;
    STATE.inited = true;
    document.title = "AuraStudy";
    var backfilled = ensureProfileDefaults();
    injectSettingsCardStyles();
    renderFocusSettingsUI();
    registerServiceWorker().then(function () {
      if (isEngineActivelyRunning && document.hidden) showBackgroundControls();
    });
    if (backfilled && typeof saveStateToLocalStorageRegister === "function") {
      saveStateToLocalStorageRegister();
    }
  }

  window.AuraFocus = {
    init: init,
    renderFocusSettingsUI: renderFocusSettingsUI,
    onPreferenceToggle: onPreferenceToggle,
    playCompletionChime: playCompletionChime,
  };
})();
