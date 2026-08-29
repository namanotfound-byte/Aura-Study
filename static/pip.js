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
 * WHAT THIS FILE DOES ON ITS OWN THE INSTANT IT LOADS (no wiring needed):
 *   - Self-installing wraps (same pattern as sync.js's wrap of
 *     saveStateToLocalStorageRegister) around:
 *       toggleEngineExecutionLoop   -> opens the floating window on the
 *                                      Start/Resume gesture when the "float
 *                                      timer" preference is on (this MUST
 *                                      happen synchronously, before anything
 *                                      is awaited, or transient activation is
 *                                      lost and documentPictureInPicture
 *                                      throws NotAllowedError).
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
 *   3. A static "Focus mode" card in #view-settings with three checkboxes
 *      (ids: focus-toggle-float / focus-toggle-notify / focus-toggle-wakelock,
 *      each `data-pref="floatTimer|notify|keepAwake"`,
 *      onchange="AuraFocus.onPreferenceToggle(this)") and a
 *      `<p id="focus-mode-capability-note">` this file fills in with what the
 *      current browser actually supports.
 *   4. A "Pop out timer" button anywhere in #view-timer's controls:
 *          onclick="AuraFocus.popOutTimer()"
 *      This must be a bare, synchronous onclick (same activation rule as #1).
 *
 * DEGRADE CHAIN (see SPEC-PHASE2.md Part A):
 *   1. documentPictureInPicture.requestWindow() on the Start/Resume gesture
 *      (Chromium only).
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

  var DEFAULT_PREFS = { floatTimer: true, notify: true, keepAwake: true };

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
      ? timeText + " left on " + course + " ✨"
      : timeText + " elapsed on " + course + " ✨";
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
      var n = new Notification("AuraStudy ✨", {
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

  function wirePipEvents() {
    var els = STATE.pipEls;
    if (!els) return;
    // These call the bare global identifiers, which by the time any pip
    // window can possibly be open have already been replaced by this file's
    // own wraps below -- so a click in the floating window drives the exact
    // same start/pause/reset path a click in the main page would.
    els.resetBtn.addEventListener("click", function () {
      resetEngineDisplayState();
    });
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
    ctx.fillText("AuraStudy ✨", cx, H - 12);
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

  function maybeAutoFloatOnStart() {
    if (!prefs().floatTimer) return;
    if (STATE.pipMode) return; // already open, nothing to do
    attemptOpenFloatingWindow("auto");
  }

  function afterEngineStateChange() {
    if (isEngineActivelyRunning) requestWakeLock();
    else releaseWakeLock();
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
    if (floatEl) floatEl.checked = !!p.floatTimer;
    if (notifyEl) notifyEl.checked = !!p.notify;
    if (wakeEl) wakeEl.checked = !!p.keepAwake;
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

  // -- public: manual "Pop out timer" button -----------------------------

  function popOutTimer() {
    // Synchronous, gesture-driven -- no await before the request call.
    var opened = attemptOpenFloatingWindow("manual");
    if (!opened) {
      toast(
        "Can't Float the Timer Here",
        "This browser doesn't support a floating window. Turn on notifications in Focus Mode settings to still get a heads-up.",
        false
      );
    }
  }

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
    var wasRunning = isEngineActivelyRunning;
    if (!wasRunning) maybeAutoFloatOnStart();
    var result = original.apply(thisArg, args);
    afterEngineStateChange();
    return result;
  });

  wrapGlobalFn("resetEngineDisplayState", function (original, thisArg, args) {
    var result = original.apply(thisArg, args);
    if (!STATE.completing) endSessionCleanup();
    return result;
  });

  wrapGlobalFn("saveEngineWorkspaceBlockData", function (original, thisArg, args) {
    var beforeCount = appState.sessions ? appState.sessions.length : 0;
    STATE.completing = true;
    var result = original.apply(thisArg, args);
    STATE.completing = false;
    var logged = !!(appState.sessions && appState.sessions.length > beforeCount);
    finishSession(logged);
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
    popOutTimer: popOutTimer,
    onPreferenceToggle: onPreferenceToggle,
  };
})();
