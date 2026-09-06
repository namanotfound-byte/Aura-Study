/*
 * AuraStudy guest trial (static/guest.js)
 * ========================================
 * Reads window.__AURA_GUEST_CTX__ injected by the /app route (server/guest.py).
 * Exposes window.AuraGuest for soft-locking account-only features while keeping
 * localStorage study data intact after the 7-day window ends.
 */
(function () {
  "use strict";

  var TRIAL_MS = 7 * 24 * 60 * 60 * 1000;

  function ctx() {
    return window.__AURA_GUEST_CTX__ || { is_guest: false };
  }

  function startedAtMs() {
    var c = ctx();
    if (!c.is_guest || !c.started_at) return null;
    var ms = Date.parse(c.started_at);
    return isNaN(ms) ? null : ms;
  }

  function isGuest() {
    return !!ctx().is_guest;
  }

  function isExpired() {
    if (!isGuest()) return false;
    var start = startedAtMs();
    if (start === null) return true;
    return Date.now() >= start + TRIAL_MS;
  }

  function daysLeft() {
    if (!isGuest()) return 0;
    var start = startedAtMs();
    if (start === null) return 0;
    var remaining = start + TRIAL_MS - Date.now();
    if (remaining <= 0) return 0;
    return remaining / (24 * 60 * 60 * 1000);
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
        "Your guest trial has ended. Log in or sign up to continue studying and unlock leaderboard, Spotify, and cloud sync.";
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
    var emailDisplay = document.getElementById("user-email-display");
    if (emailDisplay) {
      var days = daysLeft();
      if (isExpired()) {
        emailDisplay.textContent = "Guest trial ended";
      } else if (days >= 1) {
        emailDisplay.textContent = "Guest · " + Math.ceil(days) + " day" + (Math.ceil(days) === 1 ? "" : "s") + " left";
      } else {
        emailDisplay.textContent = "Guest · less than a day left";
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

  window.AuraGuest = {
    isGuest: isGuest,
    isExpired: isExpired,
    daysLeft: daysLeft,
    requireAccount: requireAccount,
    hideAccountModal: hideAccountModal,
    applyGuestNav: applyGuestNav,
    guestAccountMessage: guestAccountMessage,
    showGuestLockedPanel: showGuestLockedPanel,
  };
})();
