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
 *   - It flushes pending state on `pagehide` and on `visibilitychange` ->
 *     hidden, and retries a failed push on `online` and on the next
 *     bootstrap() -- see "DURABILITY" below.
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
 *      localStorage["aurastudy_state_v1"] BEFORE the app hydrates from it:
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
 *        - if a PREVIOUS page life left an unconfirmed local edit pending
 *          (see DURABILITY below), merges it with whatever the server has
 *          and pushes the merged result, instead of letting the server's
 *          answer blindly overwrite it,
 *        - otherwise, if the server has a payload, writes it into
 *          localStorage (server wins),
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
 *   4. Call `AuraSync.flush()` for an immediate, non-debounced PUT
 *      /api/state -- e.g. right after a session is logged (index.html's
 *      saveEngineWorkspaceBlockData already does this), or after a bulk
 *      import/reset where waiting 2s would feel laggy.
 *
 * ---------------------------------------------------------------- DURABILITY
 *
 * A page can disappear at any moment -- a closed tab, a crash, a dead
 * network -- and the study time sitting only in localStorage at that instant
 * must not be silently lost. Three mechanisms work together:
 *
 *   1. TEARDOWN FLUSH (pagehide / visibilitychange -> hidden): a normal
 *      `fetch()` is CANCELLED the moment the page starts unloading, so the
 *      2s-debounced push above is not enough on its own -- close the tab
 *      inside that window and the PUT never leaves the browser.
 *      `navigator.sendBeacon` is the usual answer, but it can't set custom
 *      request headers, and this app's CSRF check (server/security.py
 *      require_csrf()) requires `X-Requested-With: XMLHttpRequest` on every
 *      state-mutating request with no exception carved out for beacons.
 *      `fetch(url, {keepalive: true})` is used instead: it both survives
 *      page teardown (the browser keeps the request alive independently of
 *      the JS context that started it) AND allows the same custom headers a
 *      normal fetch does. The tradeoff is a small combined body-size cap
 *      across in-flight keepalive requests (~64KB in Chromium) -- most study
 *      state fits well within that, but a very large payload (near
 *      state.py's 1MB limit) may not survive an unload this way. That's
 *      caught by mechanism 3 below, not silently dropped.
 *      `beforeunload` alone is NOT used: it's unreliable on mobile, where
 *      `pagehide`/`visibilitychange` are the events that actually fire
 *      (notably on iOS Safari, which often never fires `beforeunload` for a
 *      tab close or app switch at all).
 *
 *   2. PERSISTED PENDING FLAG (aurastudy_sync_meta_v1 in localStorage): every
 *      local save marks a `pendingSince` timestamp, cleared only once a push
 *      is CONFIRMED successful (a 2xx response actually processed, not just
 *      "we sent a request"). This survives a reload/crash by design -- it's
 *      what lets bootstrap() (mechanism 3) tell a fully-synced boot apart
 *      from one where the last page life left work unconfirmed.
 *
 *   3. RETRY ON RECONNECT / NEXT LOAD: a failed push (offline, a 5xx, a cold
 *      -start timeout) leaves `pendingSince` set and the local payload
 *      untouched, so it retries: on the next debounced save, on the
 *      `online` event firing (regains connectivity), and -- covering the
 *      case where the tab never comes back at all -- on bootstrap() the next
 *      time this account's app loads anywhere, which reconciles instead of
 *      overwriting (see the 409-conflict note below).
 *
 * ------------------------------------------------------------ 409 CONFLICTS
 *
 * A conflict means the server has a version of this user's state that this
 * push's `version` didn't account for -- another tab, another device, or an
 * owner-added time correction (server/admin.py bumps user_state.version
 * specifically so a stale client hits this path instead of clobbering the
 * correction). The old behaviour resolved a conflict by adopting the
 * server's payload/version and blindly RETRYING WITH THE SAME STALE LOCAL
 * PAYLOAD -- which overwrites whatever the server had with a payload that,
 * by definition, doesn't know about it. That silently drops sessions the
 * server had already accepted. Conflicts are now resolved by MERGING: the
 * two `sessions` arrays are unioned (deduped by each session's `clientId`,
 * or full content for legacy entries without one -- see index.html's
 * unionMissingSessions), so a session either side knows about survives
 * either way. The merged payload is what's retried, and the currently-open
 * tab's own in-memory appState is updated to match via
 * `window.applyMergedSyncPayloadToAppState` (see index.html) so a mid-
 * session conflict doesn't leave the UI silently stale until reload.
 *
 * All fetches include `credentials: 'same-origin'` and the
 * `X-Requested-With: XMLHttpRequest` header, per spec section 9.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "aurastudy_state_v1";
  var LEGACY_STORAGE_KEY = "aurastudy_girly_v8"; // pre-rebrand key name; fall back to it on read so existing local data isn't lost
  var META_STORAGE_KEY = "aurastudy_sync_meta_v1";
  var DEBOUNCE_MS = 2000;

  var state = {
    version: 0,
    debounceTimer: null,
    pushInFlight: false,
    pushAgainAfter: false,
  };

  // -------------------------------------------------------------- meta/pending

  function readMeta() {
    try {
      var raw = localStorage.getItem(META_STORAGE_KEY);
      if (!raw) return { version: 0, pendingSince: null };
      var parsed = JSON.parse(raw);
      return {
        version: typeof parsed.version === "number" ? parsed.version : 0,
        pendingSince: typeof parsed.pendingSince === "number" ? parsed.pendingSince : null,
      };
    } catch (e) {
      return { version: 0, pendingSince: null };
    }
  }

  function writeMeta(meta) {
    try {
      localStorage.setItem(META_STORAGE_KEY, JSON.stringify(meta));
    } catch (e) {}
  }

  function markPending() {
    var meta = readMeta();
    if (meta.pendingSince === null) {
      meta.pendingSince = Date.now();
      writeMeta(meta);
    }
  }

  function markSynced(newVersion) {
    state.version = newVersion;
    writeMeta({ version: newVersion, pendingSince: null });
  }

  function isPending() {
    return readMeta().pendingSince !== null;
  }

  // -------------------------------------------------------------- local I/O

  function readLocalPayload() {
    var raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      // Fall back to the legacy pre-rebrand key so existing local data isn't
      // lost, then write it forward under the new key name.
      raw = localStorage.getItem(LEGACY_STORAGE_KEY);
      if (raw) {
        try {
          localStorage.setItem(STORAGE_KEY, raw);
        } catch (e) {}
      }
    }
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

  // -------------------------------------------------------------- session merge

  function sessionSignature(s) {
    if (!s) return "";
    return [s.date, s.course, s.type, s.durationSeconds, s.timestamp, s.addedByAdmin ? 1 : 0].join("|");
  }

  function sessionMergeKey(s) {
    return s && s.clientId ? "id:" + s.clientId : "sig:" + sessionSignature(s);
  }

  // Union of two `sessions` arrays, deduped by sessionMergeKey. Only ever
  // ADDS entries -- never drops one from either side. `base`'s non-sessions
  // fields (profile, courses, todoItems, etc.) win as-is: those aren't
  // multi-writer append-only structures the way sessions are, and `base` is
  // always the side representing this device's own most recent edit.
  function mergePayloads(base, other) {
    var merged = base && typeof base === "object" ? JSON.parse(JSON.stringify(base)) : {};
    var baseSessions = Array.isArray(merged.sessions) ? merged.sessions : [];
    var otherSessions = other && Array.isArray(other.sessions) ? other.sessions : [];
    var seen = {};
    baseSessions.forEach(function (s) {
      seen[sessionMergeKey(s)] = true;
    });
    var missing = otherSessions.filter(function (s) {
      var key = sessionMergeKey(s);
      if (seen[key]) return false;
      seen[key] = true;
      return true;
    });
    merged.sessions = baseSessions.concat(missing);
    return merged;
  }

  function notifyAppOfMerge(mergedPayload) {
    if (typeof window.applyMergedSyncPayloadToAppState === "function") {
      try {
        window.applyMergedSyncPayloadToAppState(mergedPayload);
      } catch (e) {}
    }
  }

  // -------------------------------------------------------------- misc helpers

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

  function refreshSyncStatus() {
    setSyncStatus(isPending() ? "Sync pending…" : "Synced ✓");
  }

  function toast(title, desc) {
    if (typeof window.triggerAlertToast === "function") {
      window.triggerAlertToast(title, desc, false);
    }
  }

  function goToLogin() {
    window.location.replace("/login");
  }

  function localDateParam() {
    // See index.html's getLocalDateStr -- keeps the server's notion of
    // "this week" (server/leaderboard.py:current_week_start) agreeing with
    // the client's own local calendar date that session.date values use.
    try {
      return typeof window.getLocalDateStr === "function" ? window.getLocalDateStr() : undefined;
    } catch (e) {
      return undefined;
    }
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
      keepalive: !!options.keepalive,
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
      return r.data;
    });
  }

  function putServerState(payload, version, keepalive) {
    var body = { payload: payload, version: version };
    var ld = localDateParam();
    if (ld) body.local_date = ld;
    return authedFetch("/api/state", {
      method: "PUT",
      body: JSON.stringify(body),
      keepalive: keepalive,
    });
  }

  // -------------------------------------------------------------- bootstrap

  function bootstrap() {
    setSyncStatus("Syncing...");
    return authedFetch("/api/auth/me")
      .then(function (meResult) {
        var user = meResult.data.user;
        var meta = readMeta();
        state.version = meta.version;

        return fetchServerState().then(function (stateData) {
          var serverVersion = stateData.version || 0;

          if (meta.pendingSince !== null) {
            // A previous page life made a local edit that never confirmed
            // as pushed (crash, force-quit, or simply offline the whole
            // time it was open). The server's payload here may be stale
            // relative to that edit, so it must NOT blindly overwrite
            // localStorage the way the clean-boot path below does -- merge
            // instead, then push the merged result now.
            var local = readLocalPayload();
            if (local) {
              var merged = stateData.payload ? mergePayloads(local, stateData.payload) : local;
              writeLocalPayload(merged);
              state.version = serverVersion;
              return putServerState(merged, serverVersion)
                .then(function (putResult) {
                  if (putResult.res.ok) {
                    markSynced(putResult.data.version);
                  } else if (putResult.res.status === 409) {
                    // Raced again on the very first retry -- leave it
                    // pending; the debounced push / 'online' listener / next
                    // bootstrap() keeps trying with a fresh merge each time.
                    state.version = putResult.data.version;
                  }
                  return user;
                })
                .catch(function () {
                  return user; // still offline -- stays pending
                });
            }
            // No local payload despite a pending flag (shouldn't normally
            // happen) -- fall through to the clean-boot path below.
          }

          if (stateData.payload) {
            writeLocalPayload(stateData.payload);
            markSynced(serverVersion);
            return user;
          }
          var localFresh = readLocalPayload();
          if (!localFresh) {
            markSynced(serverVersion);
            return user;
          }
          // Guard against cross-account leakage: localStorage is shared per
          // *origin*, not per account, so on a shared browser it can still
          // hold the PREVIOUS logged-in user's cached data after a logout.
          // Only auto-migrate local data up when it is unowned (no
          // profile.email stamped on it yet -- the true first-time,
          // pre-auth -> account migration) or it already belongs to this
          // same user. Anything else gets dropped rather than pushed to the
          // wrong account.
          var localOwner = localFresh && localFresh.profile && localFresh.profile.email;
          if (localOwner && user && localOwner !== user.email) {
            localStorage.removeItem(STORAGE_KEY);
            writeMeta({ version: serverVersion, pendingSince: null });
            return user;
          }
          return putServerState(localFresh, serverVersion).then(function (putResult) {
            if (putResult.res.ok) {
              markSynced(putResult.data.version);
              toast("Synced", "Your local study data is now saved to your account.");
            } else {
              // Migration push failed (offline / 5xx) -- keep the local copy
              // and retry on the next load rather than losing track of it.
              state.version = serverVersion;
              markPending();
            }
            return user;
          });
        });
      })
      .then(function (user) {
        refreshSyncStatus();
        return user;
      })
      .catch(function () {
        // 401 already redirected to /login; any other error just means we
        // boot from whatever is cached in localStorage instead.
        setSyncStatus("");
        return null;
      });
  }

  // -------------------------------------------------------------- push

  function doPush() {
    if (state.pushInFlight) {
      state.pushAgainAfter = true;
      return;
    }
    var payload = readLocalPayload();
    if (!payload) return;

    markPending(); // idempotent -- keeps pendingSince at its earliest timestamp until confirmed synced
    state.pushInFlight = true;
    setSyncStatus("Syncing...");

    putServerState(payload, state.version)
      .then(function (r) {
        if (r.res.status === 409) {
          return handleConflict(payload, r.data);
        }
        if (r.res.ok) {
          markSynced(r.data.version);
        }
        // Any other non-ok status (5xx, 413, a stale-401 race, etc.) is
        // simply left pending -- retried by the next debounced push, the
        // 'online' listener, or the next bootstrap().
      })
      .catch(function () {
        /* network hiccup, offline, or 401 (already redirected) -- stays pending */
      })
      .then(function () {
        state.pushInFlight = false;
        refreshSyncStatus();
        if (state.pushAgainAfter) {
          state.pushAgainAfter = false;
          push();
        }
      });
  }

  function handleConflict(localPayload, conflictData) {
    var merged = mergePayloads(localPayload, conflictData.payload);
    writeLocalPayload(merged);
    notifyAppOfMerge(merged);
    state.version = conflictData.version;
    return putServerState(merged, state.version).then(function (retry) {
      if (retry.res.ok) {
        markSynced(retry.data.version);
      } else if (retry.res.status === 409) {
        // Someone else won again -- abandon this cycle quietly (matches the
        // original behaviour) rather than looping forever, but stays marked
        // pending so the next debounced push / reconnect / reload retries
        // with a fresh merge instead of the change being forgotten.
        state.version = retry.data.version;
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

  // ---------------------------------------------------------- teardown flush

  // See the DURABILITY comment at the top of this file for why this is a
  // keepalive fetch and not navigator.sendBeacon.
  function keepaliveFlush() {
    var payload = readLocalPayload();
    if (!payload) return;
    if (state.debounceTimer) {
      clearTimeout(state.debounceTimer);
      state.debounceTimer = null;
    }
    markPending();
    try {
      putServerState(payload, state.version, true)
        .then(function (r) {
          if (r.res.ok) markSynced(r.data.version);
          else if (r.res.status === 409) {
            // Can't safely run the full merge round-trip from a teardown
            // handler (the page may already be gone before it resolves) --
            // leave it pending. The next load's bootstrap() reconciles
            // properly, or the 'online'/visible-again flush retries.
            state.version = r.data.version;
          }
        })
        .catch(function () {});
    } catch (e) {}
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) keepaliveFlush();
  });
  window.addEventListener("pagehide", keepaliveFlush);
  window.addEventListener("online", function () {
    flush();
  });

  // -------------------------------------------------------------- logout

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
          localStorage.removeItem(LEGACY_STORAGE_KEY);
          localStorage.removeItem(META_STORAGE_KEY);
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
