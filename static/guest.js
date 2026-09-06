/*
 * AuraStudy guest mode (static/guest.js)
 * ======================================
 * Reads window.__AURA_GUEST_CTX__ injected by the /app route (server/guest.py).
 * Guests use the timer free forever with localStorage; account-only features
 * (Spotify, Help, cloud sync, appearing on leaderboards) stay locked while
 * leaderboard views remain read-only.
 */
(function () {
  "use strict";

  var NUDGE_INTERVAL_HOURS = 2.5;
  var NUDGE_STORAGE_KEY = "aurastudy_guest_nudge_hours";

  function ctx() {
    return window.__AURA_GUEST_CTX__ || { is_guest: false };
  }

  function isGuest() {
    return !!ctx().is_guest;
  }

  function isExpired() {
    return false;
  }

  function daysLeft() {
    return 0;
  }

  function ensureModal() {
    var el = document.getElementById("guest-account-modal");
    if (el) return el;
    el = document.createElement("div");
    el.id = "guest-account-modal";
    el.className = "guest-account-modal";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-labelledby", "guest-account-modal-title");
    el.innerHTML =
      '<div class="guest-account-modal-card">' +
      '<h3 id="guest-account-modal-title">Create a free account</h3>' +
      '<p id="guest-account-modal-body"></p>' +
      '<p class="guest-account-modal-note">Completely free. No credit card. Nothing required.</p>' +
      '<div class="guest-account-modal-actions">' +
      '<a href="/register" class="btn btn-neon-pink guest-account-modal-primary">Sign up free</a>' +
      '<a href="/login" class="btn guest-account-modal-secondary">Log in</a>' +
      '<button type="button" class="btn guest-account-modal-dismiss" id="guest-account-modal-close">Not now</button>' +
      "</div></div>";
    document.body.appendChild(el);
    el.addEventListener("click", function (e) {
      if (e.target === el) hideAccountModal();
    });
    var closeBtn = document.getElementById("guest-account-modal-close");
    if (closeBtn) closeBtn.addEventListener("click", hideAccountModal);
    return el;
  }

  function hideAccountModal() {
    var el = document.getElementById("guest-account-modal");
    if (el) el.classList.remove("visible");
  }

  function requireAccount(message) {
    var modal = ensureModal();
    var body = document.getElementById("guest-account-modal-body");
    if (body) {
      body.textContent =
        message ||
        "Create a free account to unlock Spotify, Help, cloud sync, and appearing on the leaderboard.";
    }
    modal.classList.add("visible");
  }

  function applyGuestNav() {
    if (!isGuest()) return;
    var logoutNav = document.getElementById("nav-item-logout");
    if (logoutNav) {
      logoutNav.onclick = function () {
        window.location.href = "/register";
      };
      var label = logoutNav.querySelector("span");
      if (label) label.textContent = "Sign up / Log in";
      var icon = logoutNav.querySelector("[data-lucide]");
      if (icon) {
        icon.setAttribute("data-lucide", "user-plus");
        if (window.lucide) lucide.createIcons();
      }
    }
  }

  function guestAccountMessage(featureLabel) {
    return (
      "Create a free account to use " +
      featureLabel +
      ". Completely free — no credit card, nothing required."
    );
  }

  function showGuestLockedPanel(containerId, featureLabel) {
    var panel = document.getElementById(containerId);
    if (!panel) return;
    panel.innerHTML =
      '<div class="card lb-state-panel guest-locked-panel">' +
      '<i data-lucide="lock"></i>' +
      "<span>" +
      guestAccountMessage(featureLabel) +
      "</span>" +
      '<div class="guest-locked-actions">' +
      '<a href="/register" class="btn btn-neon-pink">Sign up free</a>' +
      '<a href="/login" class="btn">Log in</a>' +
      "</div></div>";
    if (window.lucide) lucide.createIcons();
  }

  function readLastNudgeHours() {
    try {
      var raw = localStorage.getItem(NUDGE_STORAGE_KEY);
      var val = parseFloat(raw);
      return isFinite(val) && val >= 0 ? val : 0;
    } catch (e) {
      return 0;
    }
  }

  function writeLastNudgeHours(hours) {
    try {
      localStorage.setItem(NUDGE_STORAGE_KEY, String(hours));
    } catch (e) {
      /* ignore */
    }
  }

  function maybePromptGuestLogin(totalStudySeconds) {
    if (!isGuest()) return;
    if (typeof totalStudySeconds !== "number" || totalStudySeconds <= 0) return;
    var totalHours = totalStudySeconds / 3600;
    if (totalHours < NUDGE_INTERVAL_HOURS) return;

    var currentThreshold =
      Math.floor(totalHours / NUDGE_INTERVAL_HOURS) * NUDGE_INTERVAL_HOURS;
    var lastNudgeHours = readLastNudgeHours();
    if (currentThreshold <= lastNudgeHours) return;

    writeLastNudgeHours(currentThreshold);

    if (typeof showAuraConfirmDialog !== "function") return;

    showAuraConfirmDialog({
      title: "Would you like to log in?",
      message:
        "You have been studying as a guest. Log in or sign up free to sync your progress, appear on the leaderboard, and unlock Spotify and Help.",
      confirmLabel: "Log in",
      cancelLabel: "Keep studying",
    }).then(function (confirmed) {
      if (confirmed) {
        window.location.href = "/login?next=%2Fapp";
      }
    });
  }

  window.AuraGuest = {
    isGuest: isGuest,
    isExpired: isExpired,
    daysLeft: daysLeft,
    requireAccount: requireAccount,
    hideAccountModal: hideAccountModal,
    applyGuestNav: applyGuestNav,
    guestAccountMessage: guestAccountMessage,
    showGuestLockedPanel: showGuestLockedPanel,
    maybePromptGuestLogin: maybePromptGuestLogin,
  };
})();
