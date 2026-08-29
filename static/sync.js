/*
 * AuraStudy server <-> localStorage sync (static/sync.js)
 * =========================================================
 * Exposes a single global: window.AuraSync
 * Plain ES2018 browser JS, no build step, no module system.
 *
 * WHAT THIS FILE DOES ON ITS OWN (no wiring needed in index.html):
 *   - The instant this script runs, it WRAPS (never replaces) the existing
 *     global `saveStateToLocalStorageRegister()`. The app already calls that
 *     function on every state mutation; after this file loads, each call
 *     ALSO schedules a debounced (2s) `PUT /api/state`. The original
 *     synchronous localStorage write is untouched.
 *   - It lazily creates a tiny "Syncing... / Synced" text node right under
 *     the sidebar's `#user-email-display` element and keeps it updated, as
 *     the "unobtrusive sync indicator" required by spec section 9.
 *
 * WHAT AGENT C MUST WIRE UP IN index.html:
 *
 *   1. Load this AFTER the existing inline app <script> (so appState /
 *      saveStateToLocalStorageRegister / loadStateFromLocalStorageRegister /
 *      switchView already exist as globals) and BEFORE spotify.js:
 *
 *          <script src="/static/sync.js"></script>
 *          <script src="/static/spotify.js"></script>
 *
 *   2. The app's own DOMContentLoaded handler currently calls
 *      loadStateFromLocalStorageRegister() synchronously, first thing. That
 *      call must instead wait on AuraSync.bootstrap() so that, if the server
 *      has a copy of this user's state, it gets written into
 *      localStorage["aurastudy_girly_v8"] BEFORE the app hydrates from it:
 *
 *          window.addEventListener('DOMContentLoaded', () => {
 *            AuraSync.bootstrap().finally(() => {
 *              loadStateFromLocalStorageRegister();
 *              lucide.createIcons();
 *              // ...rest of the existing boot sequence, unchanged
 *            });
 *          });
 *
 *      AuraSync.bootstrap() returns a Promise that:
 *        - calls GET /api/auth/me (redirects to /login on 401 and never
 *          resolves further -- the page is navigating away),
 *        - calls GET /api/state,
 *        - if the server has a payload, writes it into localStorage (server
 *          wins),
 *        - if the server has no payload yet but localStorage does, PUTs the
 *          local copy up to the server (one-time local -> account
 *          migration on first login),
 *        - resolves with the `user` object from /api/auth/me (or `null` if
 *          it bailed out early). The promise itself never rejects, so a
 *          bare `.finally()` is always safe; use `.then(user => ...)` if you
 *          want the user object too (e.g. to populate
 *          `#user-email-display` -- this file does not do that for you).
 *
 *   3. Wire the sidebar "Log out" control to `AuraSync.logout()` instead of
 *      a hand-rolled fetch: it flushes any pending state, POSTs
 *      /api/auth/logout with the required CSRF header, and redirects to
 *      /login.
 *
 *   4. Optional: call `AuraSync.flush()` for an immediate, non-debounced
 *      PUT /api/state -- e.g. on `beforeunload`, or right after a bulk
 *      import/reset where waiting 2s would feel laggy.
 *
 * ERROR HANDLING THIS FILE ALREADY DOES:
 *   - Any 401 from /api/auth/me or /api/state (GET or PUT) redirects to
 *     /login via `window.location.replace`.
 *   - A 409 conflict on PUT is resolved by adopting the server's returned
 *     payload/version and retrying the PUT once with the fresh version. If
 *     that retry also conflicts, this push cycle is abandoned quietly (the
 *     next debounced push -- triggered by the next local save -- tries
 *     again), rather than looping forever.
 *
 * All fetches include `credentials: 'same-origin'` and the
 * `X-Requested-With: XMLHttpRequest` header, per spec section 9.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "aurastudy_girly_v8";
  var DEBOUNCE_MS = 2000;

  var state = {
    version: 0,
    debounceTimer: null,
    pushInFlight: false,
    pushAgainAfter: false,
  };

  function readLocalPayload() {
    var raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function writeLocalPayload(payload) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  }

  function ensureIndicator() {
    var el = document.getElementById("aura-sync-indicator");
    if (el) return el;
    var anchor = document.getElementById("user-email-display");
    if (!anchor || !anchor.parentNode) return null;
    el = document.createElement("span");
    el.id = "aura-sync-indicator";
    el.style.cssText = "display:block;font-size:11px;opacity:0.65;margin-top:2px;";
    anchor.parentNode.appendChild(el);
    return el;
  }

  function setSyncStatus(text) {
    var el = ensureIndicator();
    if (el) el.textContent = text;
  }

  function toast(title, desc) {
    if (typeof window.triggerAlertToast === "function") {
      window.triggerAlertToast(title, desc, false);
    }
  }

  function goToLogin() {
    window.location.replace("/login");
  }

  function authedFetch(path, options) {
    options = options || {};
    var headers = { "X-Requested-With": "XMLHttpRequest" };
    for (var k in options.headers || {}) headers[k] = options.headers[k];
    if (options.body) headers["Content-Type"] = "application/json";

    return fetch(path, {
      method: options.method || "GET",
      credentials: "same-origin",
      headers: headers,
      body: options.body,
    }).then(function (res) {
      if (res.status === 401) {
        goToLogin();
        return Promise.reject(new Error("unauthenticated"));
      }
      return res
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          return { res: res, data: data };
        });
    });
  }

  function fetchServerState() {
    return authedFetch("/api/state").then(function (r) {
      state.version = r.data.version || 0;
      return r.data;
    });
  }

  function putServerState(payload, version) {
    return authedFetch("/api/state", {
      method: "PUT",
      body: JSON.stringify({ payload: payload, version: version }),
    });
  }

  function bootstrap() {
    setSyncStatus("Syncing...");
    return authedFetch("/api/auth/me")
      .then(function (meResult) {
        var user = meResult.data.user;
        return fetchServerState().then(function (stateData) {
          if (stateData.payload) {
            writeLocalPayload(stateData.payload);
            return user;
          }
          var local = readLocalPayload();
          if (!local) return user;
          // Guard against cross-account leakage: localStorage is shared per
          // *origin*, not per account, so on a shared browser it can still
          // hold the PREVIOUS logged-in user's cached data after a logout.
          // Only auto-migrate local data up when it is unowned (no
          // profile.email stamped on it yet -- the true first-time,
          // pre-auth -> account migration) or it already belongs to this
          // same user. Anything else gets dropped rather than pushed to the
          // wrong account.
          var localOwner = local && local.profile && local.profile.email;
          if (localOwner && user && localOwner !== user.email) {
            localStorage.removeItem(STORAGE_KEY);
            return user;
          }
          return putServerState(local, 0).then(function (putResult) {
            if (putResult.res.ok) {
              state.version = putResult.data.version;
              toast("Synced ✨", "Your local study data is now saved to your account.");
            }
            return user;
          });
        });
      })
      .then(function (user) {
        setSyncStatus("Synced ✓");
        return user;
      })
      .catch(function () {
        // 401 already redirected to /login; any other error just means we
        // boot from whatever is cached in localStorage instead.
        setSyncStatus("");
        return null;
      });
  }

  function doPush() {
    if (state.pushInFlight) {
      state.pushAgainAfter = true;
      return;
    }
    var payload = readLocalPayload();
    if (!payload) return;

    state.pushInFlight = true;
    setSyncStatus("Syncing...");

    putServerState(payload, state.version)
      .then(function (r) {
        if (r.res.status === 409) {
          state.version = r.data.version;
          if (r.data.payload) writeLocalPayload(r.data.payload);
          return putServerState(payload, state.version).then(function (retry) {
            if (retry.res.ok) state.version = retry.data.version;
          });
        }
        if (r.res.ok) {
          state.version = r.data.version;
        }
      })
      .catch(function () {
        /* network hiccup, or 401 (already redirected) -- next debounced push retries */
      })
      .then(function () {
        state.pushInFlight = false;
        setSyncStatus("Synced ✓");
        if (state.pushAgainAfter) {
          state.pushAgainAfter = false;
          push();
        }
      });
  }

  function push() {
    if (state.debounceTimer) clearTimeout(state.debounceTimer);
    state.debounceTimer = setTimeout(function () {
      state.debounceTimer = null;
      doPush();
    }, DEBOUNCE_MS);
  }

  function flush() {
    if (state.debounceTimer) {
      clearTimeout(state.debounceTimer);
      state.debounceTimer = null;
    }
    doPush();
  }

  function logout() {
    flush();
    return authedFetch("/api/auth/logout", { method: "POST", body: "{}" })
      .catch(function () {})
      .then(function () {
        // Defense in depth against cross-account leakage on a shared
        // browser: don't leave this account's cached study data sitting in
        // localStorage (shared per-origin) for whoever logs in next.
        try {
          localStorage.removeItem(STORAGE_KEY);
        } catch (e) {}
        window.location.href = "/login";
      });
  }

  // Self-installing wrap: every existing call to saveStateToLocalStorageRegister()
  // now also schedules a debounced server push. Nothing else about it changes.
  if (typeof window.saveStateToLocalStorageRegister === "function") {
    var originalSave = window.saveStateToLocalStorageRegister;
    window.saveStateToLocalStorageRegister = function () {
      var result = originalSave.apply(this, arguments);
      push();
      return result;
    };
  }

  window.AuraSync = {
    bootstrap: bootstrap,
    push: push,
    flush: flush,
    logout: logout,
  };
})();
