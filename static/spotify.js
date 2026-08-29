/*!
 * static/spotify.js — AuraStudy Spotify panel
 *
 * Exposes a single global: window.AuraSpotify
 *   AuraSpotify.init()                 - call once, after `GET /api/auth/me`
 *                                         succeeds (same place sync.js boots).
 *                                         Builds the Spotify panel and the
 *                                         Timer-view now-playing strip, and
 *                                         handles the ?spotify=connected /
 *                                         ?spotify=error redirect params.
 *   AuraSpotify.onViewShown()          - call when the Spotify nav view
 *                                         becomes active. Starts a 5s
 *                                         now-playing poll.
 *   AuraSpotify.onViewHidden()         - call when navigating AWAY from the
 *                                         Spotify view. Stops that poll.
 *   AuraSpotify.renderInto(el)         - (re)builds the panel markup inside
 *                                         a given container. init() already
 *                                         calls this for #view-spotify, so
 *                                         you normally never need to call it
 *                                         directly.
 *
 * ---------------------------------------------------------------------------
 * DOM CONTRACT for Agent C (index.html) — this file builds all inner markup
 * itself; the only requirement is that these two EMPTY elements exist before
 * AuraSpotify.init() runs:
 *
 *   1. #view-spotify
 *      An empty view panel, sibling of the other views, e.g.:
 *        <div id="view-spotify" class="view-panel"></div>
 *      Add a matching sidebar nav item after "Trophies Cabinet":
 *        <div class="nav-item" onclick="switchView('spotify', this)">
 *          <i data-lucide="music"></i> Music
 *        </div>
 *      (mirror whatever wrapper markup the other .nav-item entries use).
 *
 *   2. #timer-now-playing
 *      An empty anchor div placed inside #view-timer, away from the timer
 *      controls (e.g. a top corner):
 *        <div id="timer-now-playing"></div>
 *      This strip auto-detects its own visibility with IntersectionObserver
 *      and polls on its own — no extra JS calls needed for it.
 *
 * FUNCTION CALLS Agent C must add:
 *   - Once on boot (after the auth gate's `/api/auth/me` succeeds):
 *       AuraSpotify.init();
 *   - Inside the existing switchView(targetPanelKey, ...) function:
 *       * before switching panels, if the panel currently active is
 *         'view-spotify' and targetPanelKey !== 'spotify':
 *           AuraSpotify.onViewHidden();
 *       * after activating the new panel, if targetPanelKey === 'spotify':
 *           AuraSpotify.onViewShown();
 *
 * Nothing else in index.html needs to change. This file injects its own
 * <style> block, so no CSS file edits are required either.
 * ---------------------------------------------------------------------------
 */
(function () {
  'use strict';

  var POLL_MS = 5000;
  var SDK_SRC = 'https://sdk.scdn.co/spotify-player.js';

  var STATE = {
    inited: false,
    status: null,
    viewEl: null,
    stripEl: null,
    pollTimer: null,
    sdkPromise: null,
    player: null,
    deviceId: null,
    selectedPlaylistId: null,
  };

  // -- fetch helpers ---------------------------------------------------

  function api(url, opts) {
    opts = opts || {};
    var headers = Object.assign({ 'X-Requested-With': 'XMLHttpRequest' }, opts.headers || {});
    if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    return fetch(url, {
      method: opts.method || 'GET',
      credentials: 'same-origin',
      headers: headers,
      body: opts.body,
    }).then(function (res) {
      return res
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
    });
  }

  function toast(title, desc, good) {
    if (typeof window.triggerAlertToast === 'function') {
      window.triggerAlertToast(title, desc, good !== false);
    }
  }

  function fmtTime(ms) {
    if (!ms && ms !== 0) return '--:--';
    var s = Math.floor(ms / 1000);
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ':' + (r < 10 ? '0' : '') + r;
  }

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  // -- one-time styling --------------------------------------------------

  function injectStyles() {
    if (document.getElementById('aura-spotify-styles')) return;
    var style = document.createElement('style');
    style.id = 'aura-spotify-styles';
    style.textContent =
      '.as-card{margin-bottom:20px}' +
      '.as-row{display:flex;align-items:center;gap:16px}' +
      '.as-art{width:72px;height:72px;border-radius:16px;object-fit:cover;background:var(--bg-card-hover);flex-shrink:0}' +
      '.as-track-name{font-weight:800;color:var(--text-main);font-size:15px;margin-bottom:2px}' +
      '.as-track-artist{color:var(--text-muted);font-size:13px}' +
      '.as-progress{height:6px;border-radius:6px;background:var(--border-color);margin-top:10px;overflow:hidden}' +
      '.as-progress-fill{height:100%;background:linear-gradient(to right,var(--neon-pink),var(--neon-purple));border-radius:6px;transition:width .4s linear}' +
      '.as-time-row{display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted);margin-top:4px}' +
      '.as-controls{display:flex;align-items:center;gap:12px;margin-top:16px}' +
      '.as-icon-btn{background:var(--bg-card);border:1px solid var(--border-color);color:var(--text-main);border-radius:50%;width:40px;height:40px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;transition:all .2s ease}' +
      '.as-icon-btn:hover{border-color:var(--neon-pink);transform:translateY(-1px)}' +
      '.as-icon-btn.as-play{background:var(--neon-pink);border-color:var(--neon-pink);color:#fff;width:48px;height:48px}' +
      '.as-volume{display:flex;align-items:center;gap:10px;margin-top:14px}' +
      '.as-volume input{flex:1;accent-color:var(--neon-pink)}' +
      '.as-note{font-size:12px;color:var(--text-muted);margin-top:10px;line-height:1.5}' +
      '.as-playlist-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:14px;margin-top:10px}' +
      '.as-playlist-card{cursor:pointer;border-radius:16px;overflow:hidden;border:2px solid var(--border-color);background:var(--bg-card);transition:all .2s ease}' +
      '.as-playlist-card:hover{border-color:var(--neon-pink);transform:translateY(-2px)}' +
      '.as-playlist-card.as-selected{border-color:var(--neon-pink);box-shadow:0 0 0 2px var(--neon-pink) inset}' +
      '.as-playlist-card img{width:100%;aspect-ratio:1/1;object-fit:cover;display:block;background:var(--bg-card-hover)}' +
      '.as-playlist-meta{padding:8px 10px}' +
      '.as-playlist-meta .as-pl-name{font-size:12px;font-weight:700;color:var(--text-main);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.as-playlist-meta .as-pl-count{font-size:10px;color:var(--text-muted)}' +
      '.as-embed-wrap{margin-top:16px;border-radius:16px;overflow:hidden}' +
      '.as-strip{position:absolute;top:18px;right:24px;z-index:5;display:flex;align-items:center;gap:10px;background:var(--bg-card);border:1px solid var(--border-color);border-radius:999px;padding:6px 14px 6px 6px;box-shadow:0 6px 18px rgba(var(--accent-rgb),0.12);max-width:280px}' +
      '.as-strip img{width:32px;height:32px;border-radius:50%;object-fit:cover;flex-shrink:0}' +
      '.as-strip-text{overflow:hidden}' +
      '.as-strip-title{font-size:11px;font-weight:800;color:var(--text-main);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.as-strip-artist{font-size:10px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      '.as-strip.as-hidden{display:none}';
    document.head.appendChild(style);
  }

  // -- redirect param handling --------------------------------------------

  function handleRedirectParams() {
    var params = new URLSearchParams(window.location.search);
    if (!params.has('spotify')) return;
    var val = params.get('spotify');
    if (val === 'connected') {
      toast('Spotify Connected ✨', 'Your account is linked. Enjoy the tunes!', true);
    } else if (val === 'error') {
      toast('Spotify Connection Failed', 'Reason: ' + (params.get('reason') || 'unknown') + '. Please try again.', false);
    }
    params.delete('spotify');
    params.delete('reason');
    var qs = params.toString();
    var newUrl = window.location.pathname + (qs ? '?' + qs : '') + window.location.hash;
    window.history.replaceState({}, document.title, newUrl);
  }

  // -- main panel rendering ------------------------------------------------

  function renderInto(containerEl) {
    if (!containerEl) return;
    STATE.viewEl = containerEl;
    containerEl.removeEventListener('click', onPanelClick);
    containerEl.addEventListener('click', onPanelClick);
    refreshStatus();
  }

  function refreshStatus() {
    if (!STATE.viewEl) return;
    api('/api/spotify/status').then(function (r) {
      if (!r.ok) {
        if (r.status === 401) {
          window.location.replace('/login');
          return;
        }
        STATE.viewEl.innerHTML = '<div class="card as-card"><p class="as-note">Could not reach the AuraStudy server. Try refreshing.</p></div>';
        return;
      }
      STATE.status = r.data;
      renderPanel();
    });
  }

  function renderPanel() {
    var s = STATE.status;
    if (!s || !s.configured) {
      STATE.viewEl.innerHTML =
        '<div class="card as-card">' +
        '<h3 style="margin-top:0;color:var(--text-main);">Spotify isn’t set up yet 🎶</h3>' +
        '<p class="as-note">The AuraStudy owner needs to add <code>SPOTIFY_CLIENT_ID</code> and ' +
        '<code>SPOTIFY_CLIENT_SECRET</code> to their <code>.env</code> file. See the ' +
        '"Create your Spotify app" section of the README for step-by-step instructions.</p>' +
        '</div>';
      return;
    }
    if (!s.connected) {
      STATE.viewEl.innerHTML =
        '<div class="card as-card">' +
        '<h3 style="margin-top:0;color:var(--text-main);">Bring your music into AuraStudy 🎧</h3>' +
        '<p class="as-note">Connect your Spotify account to see what’s playing and control playback without leaving your study session.</p>' +
        '<button class="btn btn-neon-pink" data-action="connect" style="margin-top:12px;">Connect Spotify</button>' +
        '</div>';
      return;
    }

    var premium = !!s.premium;
    STATE.viewEl.innerHTML =
      '<div class="card as-card" id="as-now-playing-card">' +
      '<div class="card-title">Now Playing<span style="text-transform:none;font-weight:600;">' +
      esc(s.display_name || '') + (premium ? ' · Premium' : ' · Free') + '</span></div>' +
      '<div class="as-row">' +
      '<img class="as-art" id="as-art" alt="Album art" src="" style="display:none;">' +
      '<div style="flex:1;min-width:0;">' +
      '<div class="as-track-name" id="as-track-name">Nothing playing right now</div>' +
      '<div class="as-track-artist" id="as-track-artist"></div>' +
      '<div class="as-progress"><div class="as-progress-fill" id="as-progress-fill" style="width:0%;"></div></div>' +
      '<div class="as-time-row"><span id="as-time-cur">0:00</span><span id="as-time-dur">0:00</span></div>' +
      '</div></div>' +
      (premium
        ? '<div class="as-controls">' +
          '<button class="as-icon-btn" data-action="prev" title="Previous"><i data-lucide="skip-back"></i></button>' +
          '<button class="as-icon-btn as-play" data-action="playpause" id="as-playpause" title="Play/Pause"><i data-lucide="play"></i></button>' +
          '<button class="as-icon-btn" data-action="next" title="Next"><i data-lucide="skip-forward"></i></button>' +
          '</div>' +
          '<div class="as-volume"><i data-lucide="volume-2" style="color:var(--text-muted);width:16px;height:16px;"></i>' +
          '<input type="range" id="as-volume-range" min="0" max="100" value="60"></div>'
        : '<p class="as-note">Playback control needs Spotify Premium. Pick a playlist below to preview it here instead.</p>') +
      '<button class="btn" data-action="disconnect" style="margin-top:14px;font-size:12px;padding:6px 14px;">Disconnect Spotify</button>' +
      '</div>' +
      '<div class="card as-card">' +
      '<div class="card-title">Your Playlists</div>' +
      '<div class="as-playlist-grid" id="as-playlist-grid"><p class="as-note">Loading playlists…</p></div>' +
      (premium ? '' : '<div class="as-embed-wrap" id="as-embed-wrap"></div>') +
      '</div>';

    if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();

    var volEl = document.getElementById('as-volume-range');
    if (volEl) {
      volEl.addEventListener('change', function () {
        setVolume(parseInt(volEl.value, 10));
      });
    }

    loadPlaylists();
    if (premium) initPremiumPlayer();
    pollNowPlaying();
  }

  function onPanelClick(e) {
    var actionEl = e.target.closest('[data-action]');
    if (actionEl) {
      var action = actionEl.getAttribute('data-action');
      if (action === 'connect') window.location = '/api/spotify/login';
      else if (action === 'disconnect') disconnectSpotify();
      else if (action === 'playpause') togglePlayPause();
      else if (action === 'next') nextTrack();
      else if (action === 'prev') previousTrack();
      return;
    }
    var card = e.target.closest('[data-playlist-id]');
    if (card) selectPlaylist(card.getAttribute('data-playlist-id'), card.getAttribute('data-playlist-uri'));
  }

  // -- playlists -----------------------------------------------------------

  function loadPlaylists() {
    api('/api/spotify/playlists').then(function (r) {
      var grid = document.getElementById('as-playlist-grid');
      if (!grid) return;
      if (!r.ok) {
        grid.innerHTML = '<p class="as-note">Couldn’t load playlists.</p>';
        return;
      }
      var items = r.data.items || [];
      if (!items.length) {
        grid.innerHTML = '<p class="as-note">No playlists found on your Spotify account.</p>';
        return;
      }
      grid.innerHTML = items
        .map(function (p) {
          var sel = p.id === STATE.selectedPlaylistId ? ' as-selected' : '';
          var img = p.image ? esc(p.image) : '';
          return (
            '<div class="as-playlist-card' + sel + '" data-playlist-id="' + esc(p.id) + '" data-playlist-uri="' + esc(p.uri) + '">' +
            (img ? '<img src="' + img + '" alt="">' : '<div style="aspect-ratio:1/1;background:var(--bg-card-hover);"></div>') +
            '<div class="as-playlist-meta"><div class="as-pl-name">' + esc(p.name) + '</div>' +
            '<div class="as-pl-count">' + (p.tracks != null ? p.tracks + ' tracks' : '') + '</div></div>' +
            '</div>'
          );
        })
        .join('');
    });
  }

  function selectPlaylist(id, uri) {
    STATE.selectedPlaylistId = id;
    document.querySelectorAll('.as-playlist-card').forEach(function (c) {
      c.classList.toggle('as-selected', c.getAttribute('data-playlist-id') === id);
    });
    if (STATE.status && STATE.status.premium) {
      var body = { context_uri: uri };
      if (STATE.deviceId) body.device_id = STATE.deviceId;
      api('/api/spotify/play', { method: 'PUT', body: JSON.stringify(body) }).then(function (r) {
        if (!r.ok) handlePlaybackError(r);
        else setTimeout(pollNowPlaying, 500);
      });
    } else {
      var wrap = document.getElementById('as-embed-wrap');
      if (wrap) {
        wrap.innerHTML =
          '<iframe src="https://open.spotify.com/embed/playlist/' + encodeURIComponent(id) +
          '" width="100%" height="352" frameborder="0" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" loading="lazy"></iframe>';
      }
    }
  }

  // -- transport -------------------------------------------------------

  function handlePlaybackError(r) {
    var code = (r.data && r.data.error) || 'spotify_error';
    if (code === 'premium_required') toast('Premium Required', 'Playback control needs Spotify Premium.', false);
    else if (code === 'no_active_device') toast('No Active Device', 'Open Spotify on a device first, or wait for AuraStudy’s player to connect.', false);
    else if (code === 'spotify_not_connected') refreshStatus();
    else toast('Spotify Error', 'Something went wrong talking to Spotify.', false);
  }

  function togglePlayPause() {
    var btn = document.getElementById('as-playpause');
    var isPlaying = btn && btn.getAttribute('data-playing') === '1';
    var body = {};
    if (STATE.deviceId) body.device_id = STATE.deviceId;
    var req = isPlaying
      ? api('/api/spotify/pause', { method: 'PUT' })
      : api('/api/spotify/play', { method: 'PUT', body: JSON.stringify(body) });
    req.then(function (r) {
      if (!r.ok) handlePlaybackError(r);
      else setTimeout(pollNowPlaying, 400);
    });
  }

  function nextTrack() {
    api('/api/spotify/next', { method: 'POST' }).then(function (r) {
      if (!r.ok) handlePlaybackError(r);
      else setTimeout(pollNowPlaying, 500);
    });
  }

  function previousTrack() {
    api('/api/spotify/previous', { method: 'POST' }).then(function (r) {
      if (!r.ok) handlePlaybackError(r);
      else setTimeout(pollNowPlaying, 500);
    });
  }

  function setVolume(percent) {
    api('/api/spotify/volume', { method: 'PUT', body: JSON.stringify({ percent: percent }) }).then(function (r) {
      if (!r.ok) handlePlaybackError(r);
    });
  }

  function disconnectSpotify() {
    api('/api/spotify/disconnect', { method: 'POST' }).then(function (r) {
      if (r.ok) {
        stopSdkPlayer();
        toast('Spotify Disconnected', 'You can reconnect any time.', true);
        refreshStatus();
      }
    });
  }

  // -- now playing (main panel) --------------------------------------------

  function pollNowPlaying() {
    if (!STATE.viewEl || !STATE.status || !STATE.status.connected) return;
    api('/api/spotify/now-playing').then(function (r) {
      if (!r.ok) return;
      applyNowPlaying(r.data);
    });
  }

  function applyNowPlaying(data) {
    var nameEl = document.getElementById('as-track-name');
    if (!nameEl) return; // panel not currently rendered
    var art = document.getElementById('as-art');
    var artistEl = document.getElementById('as-track-artist');
    var fill = document.getElementById('as-progress-fill');
    var curEl = document.getElementById('as-time-cur');
    var durEl = document.getElementById('as-time-dur');
    var btn = document.getElementById('as-playpause');

    if (data.track) {
      nameEl.textContent = data.track.name || '';
      artistEl.textContent = data.track.artists || '';
      if (data.track.image) {
        art.src = data.track.image;
        art.style.display = '';
      }
      var pct = data.track.duration_ms ? Math.min(100, (100 * data.track.progress_ms) / data.track.duration_ms) : 0;
      if (fill) fill.style.width = pct + '%';
      if (curEl) curEl.textContent = fmtTime(data.track.progress_ms);
      if (durEl) durEl.textContent = fmtTime(data.track.duration_ms);
    } else {
      nameEl.textContent = 'Nothing playing right now';
      artistEl.textContent = '';
      if (fill) fill.style.width = '0%';
    }
    if (btn) {
      btn.setAttribute('data-playing', data.is_playing ? '1' : '0');
      btn.innerHTML = data.is_playing ? '<i data-lucide="pause"></i>' : '<i data-lucide="play"></i>';
      if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
    }
    updateStrip(data);
  }

  // -- Web Playback SDK (Premium) ------------------------------------------

  function loadSdk() {
    if (STATE.sdkPromise) return STATE.sdkPromise;
    STATE.sdkPromise = new Promise(function (resolve) {
      if (window.Spotify) {
        resolve(window.Spotify);
        return;
      }
      window.onSpotifyWebPlaybackSDKReady = function () {
        resolve(window.Spotify);
      };
      var tag = document.createElement('script');
      tag.src = SDK_SRC;
      document.head.appendChild(tag);
    });
    return STATE.sdkPromise;
  }

  function initPremiumPlayer() {
    if (STATE.player) return;
    loadSdk().then(function (Spotify) {
      if (!Spotify || STATE.player) return;
      var player = new Spotify.Player({
        name: 'AuraStudy ✨',
        getOAuthToken: function (cb) {
          api('/api/spotify/token').then(function (r) {
            cb(r.ok ? r.data.access_token : '');
          });
        },
        volume: 0.6,
      });
      player.addListener('ready', function (evt) {
        STATE.deviceId = evt.device_id;
      });
      player.addListener('not_ready', function () {
        STATE.deviceId = null;
      });
      player.addListener('initialization_error', function (e) {
        console.warn('AuraSpotify SDK init error', e);
      });
      player.addListener('account_error', function (e) {
        console.warn('AuraSpotify SDK account error (Premium required)', e);
      });
      player.connect();
      STATE.player = player;
    });
  }

  function stopSdkPlayer() {
    if (STATE.player && typeof STATE.player.disconnect === 'function') {
      STATE.player.disconnect();
    }
    STATE.player = null;
    STATE.deviceId = null;
  }

  // -- Timer-view now-playing strip -----------------------------------------

  function renderStripSkeleton(el) {
    el.className = 'as-strip as-hidden';
    el.innerHTML =
      '<img id="as-strip-art" alt="" style="display:none;">' +
      '<div class="as-strip-text">' +
      '<div class="as-strip-title" id="as-strip-title">Not playing</div>' +
      '<div class="as-strip-artist" id="as-strip-artist"></div>' +
      '</div>';
  }

  function updateStrip(data) {
    if (!STATE.stripEl) return;
    var titleEl = document.getElementById('as-strip-title');
    var artistEl = document.getElementById('as-strip-artist');
    var art = document.getElementById('as-strip-art');
    if (!titleEl) return;
    if (data.track) {
      STATE.stripEl.classList.remove('as-hidden');
      titleEl.textContent = (data.is_playing ? '♪ ' : '⏸ ') + (data.track.name || '');
      artistEl.textContent = data.track.artists || '';
      if (data.track.image && art) {
        art.src = data.track.image;
        art.style.display = '';
      }
    } else {
      STATE.stripEl.classList.add('as-hidden');
    }
  }

  function refreshStrip() {
    if (!STATE.status || !STATE.status.connected) return;
    api('/api/spotify/now-playing').then(function (r) {
      if (r.ok) updateStrip(r.data);
    });
  }

  function setupStripObserver(stripEl) {
    if (!('IntersectionObserver' in window)) {
      refreshStrip();
      return;
    }
    var stripPoll = null;
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            refreshStrip();
            if (!stripPoll) stripPoll = setInterval(refreshStrip, POLL_MS);
          } else if (stripPoll) {
            clearInterval(stripPoll);
            stripPoll = null;
          }
        });
      },
      { threshold: 0.01 }
    );
    observer.observe(stripEl);
  }

  // -- view show/hide poll control ------------------------------------------

  function onViewShown() {
    stopPoll();
    refreshStatus();
    STATE.pollTimer = setInterval(pollNowPlaying, POLL_MS);
  }

  function stopPoll() {
    if (STATE.pollTimer) {
      clearInterval(STATE.pollTimer);
      STATE.pollTimer = null;
    }
  }

  function onViewHidden() {
    stopPoll();
  }

  // -- boot ------------------------------------------------------------

  function ensureStatusLoaded() {
    if (STATE.status) return Promise.resolve(STATE.status);
    return api('/api/spotify/status').then(function (r) {
      if (r.ok) STATE.status = r.data;
      return STATE.status;
    });
  }

  function init() {
    if (STATE.inited) return;
    STATE.inited = true;
    injectStyles();
    handleRedirectParams();

    var panel = document.getElementById('view-spotify');
    if (panel) renderInto(panel);

    var strip = document.getElementById('timer-now-playing');
    if (strip) {
      STATE.stripEl = strip;
      renderStripSkeleton(strip);
      ensureStatusLoaded().then(function () {
        setupStripObserver(strip);
      });
    }
  }

  window.AuraSpotify = {
    init: init,
    onViewShown: onViewShown,
    onViewHidden: onViewHidden,
    renderInto: renderInto,
  };
})();
