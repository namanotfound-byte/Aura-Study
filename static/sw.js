/*!
 * AuraStudy service worker — persistent study-timer notification (fallback path).
 * Registered at /sw.js (scope /). The page posts TIMER_SHOW / TIMER_UPDATE /
 * TIMER_CLEAR / TIMER_RESET_DISMISS with wall-clock anchors so this worker
 * can refresh the notification body when the tab is hidden.
 *
 * SW_VERSION: 2026-09-18-notify-v3 — single silent ongoing notification.
 */
"use strict";

var SW_VERSION = "2026-09-18-notify-v3";
var TIMER_TAG = "aurastudy-timer-ongoing";
var ICON = "/static/brand/aurastudy-icon-192.png";
var UPDATE_INTERVAL_MS = 60000;

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
var notificationDismissed = false;
var notificationActive = false;
var lastDisplayedKey = "";

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

  if (state.phase === "break") {
    var breakRemaining = Math.max(0, Math.floor((state.countdownTotalSeconds || 0) - elapsed));
    return {
      title: formatHMS(breakRemaining),
      body: "Break",
    };
  }

  if (state.mode === "countdown") {
    var total = Math.max(1, Math.floor(state.countdownTotalSeconds || 0));
    var left = Math.max(0, total - elapsed);
    return {
      title: formatHMS(left),
      body: course,
    };
  }

  return {
    title: formatHMS(elapsed),
    body: course,
  };
}

function stopNotificationUpdates() {
  if (updateTimerId !== null) {
    clearInterval(updateTimerId);
    updateTimerId = null;
  }
}

function startNotificationUpdates() {
  if (notificationDismissed) return;
  stopNotificationUpdates();
  updateTimerId = setInterval(function () {
    if (!timerState || notificationDismissed) return;
    refreshTimerNotificationIfChanged().catch(function () {});
  }, UPDATE_INTERVAL_MS);
}

function refreshTimerNotificationIfChanged(force) {
  if (notificationDismissed || !timerState) return Promise.resolve();
  var payload = buildNotificationPayload(timerState);
  var displayKey = (payload.title || "00:00") + "|" + (payload.body || "");
  if (!force && displayKey === lastDisplayedKey) return Promise.resolve();
  lastDisplayedKey = displayKey;
  return showTimerNotification(payload);
}

function showTimerNotification(payload) {
  if (notificationDismissed || !timerState) return Promise.resolve();
  var options = {
    body: payload.body,
    tag: TIMER_TAG,
    renotify: false,
    silent: true,
    requireInteraction: true,
    icon: ICON,
    badge: ICON,
    data: { kind: "timer" },
    actions: [
      { action: "pause", title: "Pause" },
      { action: "log", title: "Log" },
    ],
  };
  notificationActive = true;
  return self.registration.showNotification(payload.title || "00:00", options);
}

function clearTimerNotification() {
  stopNotificationUpdates();
  timerState = null;
  notificationActive = false;
  lastDisplayedKey = "";
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
  if (data.type === "TIMER_RESET_DISMISS") {
    notificationDismissed = false;
    return Promise.resolve();
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

  if (notificationDismissed) {
    stopNotificationUpdates();
    return Promise.resolve();
  }

  if (data.type === "TIMER_SHOW") {
    startNotificationUpdates();
    return refreshTimerNotificationIfChanged(true);
  }

  if (data.type === "TIMER_UPDATE") {
    if (!notificationActive) startNotificationUpdates();
    return refreshTimerNotificationIfChanged(true);
  }

  return Promise.resolve();
}

function broadcastToClients(message) {
  return self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
    clients.forEach(function (client) {
      try {
        client.postMessage(message);
      } catch (e) {}
    });
    return clients;
  });
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

self.addEventListener("notificationclose", function (event) {
  if (!event.notification || event.notification.tag !== TIMER_TAG) return;
  notificationDismissed = true;
  notificationActive = false;
  stopNotificationUpdates();
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var action = event.action;

  if (action === "pause") {
    event.waitUntil(
      broadcastToClients({ type: "AURASTUDY_PAUSE" }).then(function (clients) {
        for (var i = 0; i < clients.length; i++) {
          if ("focus" in clients[i]) return clients[i].focus();
        }
      })
    );
    return;
  }

  if (action === "log") {
    event.waitUntil(
      broadcastToClients({ type: "AURASTUDY_LOG" }).then(function (clients) {
        for (var i = 0; i < clients.length; i++) {
          if ("focus" in clients[i]) return clients[i].focus();
        }
      })
    );
    return;
  }

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
      for (var i = 0; i < clients.length; i++) {
        var client = clients[i];
        if (client.url.indexOf("/app") !== -1 && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow("/app");
    })
  );
});
