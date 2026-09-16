/*!
 * AuraStudy service worker — persistent study-timer notification (Clock/Spotify-style).
 * Registered at /sw.js (scope /). The page posts TIMER_SHOW / TIMER_UPDATE /
 * TIMER_CLEAR messages with wall-clock anchors so this worker can refresh the
 * notification body even when the tab is frozen or the screen is off.
 */
"use strict";

var TIMER_TAG = "aurastudy-timer-ongoing";
var ICON = "/static/brand/aurastudy-icon-192.png";
var UPDATE_INTERVAL_MS = 15000;

/** @type {null | {
 *   isRunning: boolean,
 *   anchorMs: number|null,
 *   bankedSeconds: number,
 *   mode: string,
 *   course: string,
 *   countdownTotalSeconds: number,
 *   phase: string
 * }} */
var timerState = null;
var updateTimerId = null;

function pad2(n) {
  return String(n).padStart(2, "0");
}

function formatHMS(totalSeconds) {
  var s = Math.max(0, Math.floor(totalSeconds || 0));
  var h = Math.floor(s / 3600);
  var m = Math.floor((s % 3600) / 60);
  var sec = s % 60;
  if (h > 0) return h + ":" + pad2(m) + ":" + pad2(sec);
  return pad2(m) + ":" + pad2(sec);
}

function computeElapsedSeconds(state) {
  var banked = Math.max(0, Math.floor(state.bankedSeconds || 0));
  if (state.isRunning && state.anchorMs) {
    return banked + Math.floor(Math.max(0, (Date.now() - state.anchorMs) / 1000));
  }
  return banked;
}

function buildNotificationPayload(state) {
  var elapsed = computeElapsedSeconds(state);
  var course = (state.course || "Study session").trim();
  var timeText = formatHMS(elapsed);

  if (state.phase === "break") {
    var remaining = Math.max(0, Math.floor((state.countdownTotalSeconds || 0) - elapsed));
    return {
      title: "AuraStudy — Break",
      body: formatHMS(remaining) + " left on your break",
    };
  }

  if (state.mode === "countdown") {
    var total = Math.max(1, Math.floor(state.countdownTotalSeconds || 0));
    var left = Math.max(0, total - elapsed);
    return {
      title: "AuraStudy — " + timeText,
      body: formatHMS(left) + " left · " + course,
    };
  }

  return {
    title: "AuraStudy — " + timeText,
    body: timeText + " elapsed · " + course,
  };
}

function stopNotificationUpdates() {
  if (updateTimerId !== null) {
    clearInterval(updateTimerId);
    updateTimerId = null;
  }
}

function startNotificationUpdates() {
  stopNotificationUpdates();
  updateTimerId = setInterval(function () {
    if (!timerState) return;
    showTimerNotification(buildNotificationPayload(timerState)).catch(function () {});
  }, UPDATE_INTERVAL_MS);
}

function showTimerNotification(payload) {
  var options = {
    body: payload.body,
    tag: TIMER_TAG,
    renotify: true,
    silent: true,
    requireInteraction: true,
    icon: ICON,
    badge: ICON,
    data: { url: "/app", kind: "timer" },
    actions: [{ action: "open", title: "Open AuraStudy" }],
  };
  return self.registration.showNotification(payload.title || "AuraStudy", options);
}

function clearTimerNotification() {
  stopNotificationUpdates();
  timerState = null;
  return self.registration.getNotifications({ tag: TIMER_TAG }).then(function (list) {
    list.forEach(function (n) {
      try {
        n.close();
      } catch (e) {}
    });
  });
}

function handleTimerMessage(data) {
  if (data.type === "TIMER_CLEAR") {
    return clearTimerNotification();
  }
  if (data.type !== "TIMER_SHOW" && data.type !== "TIMER_UPDATE") return Promise.resolve();

  timerState = {
    isRunning: !!data.isRunning,
    anchorMs: typeof data.anchorMs === "number" && isFinite(data.anchorMs) ? data.anchorMs : null,
    bankedSeconds: typeof data.bankedSeconds === "number" && isFinite(data.bankedSeconds) ? data.bankedSeconds : 0,
    mode: data.mode === "countdown" ? "countdown" : "stopwatch",
    course: typeof data.course === "string" ? data.course : "",
    countdownTotalSeconds:
      typeof data.countdownTotalSeconds === "number" && isFinite(data.countdownTotalSeconds)
        ? data.countdownTotalSeconds
        : 0,
    phase: data.phase === "break" ? "break" : "study",
  };

  if (data.type === "TIMER_SHOW") startNotificationUpdates();
  return showTimerNotification(buildNotificationPayload(timerState));
}

self.addEventListener("install", function (event) {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("message", function (event) {
  var data = event.data || {};
  var result = handleTimerMessage(data);
  if (result && typeof result.then === "function" && event.waitUntil) {
    event.waitUntil(result);
  }
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var targetUrl = (event.notification && event.notification.data && event.notification.data.url) || "/app";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
      for (var i = 0; i < clients.length; i++) {
        var client = clients[i];
        if (client.url.indexOf("/app") !== -1 && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
    })
  );
});
