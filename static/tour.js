/* AuraStudy first-run spotlight tour — landing + /app product walkthrough.
 * Vanilla overlay (#aura-tour-overlay); no external libraries.
 * Persists completion in localStorage (aurastudy_tour_done). */
(function () {
    'use strict';

    var TOUR_DONE_KEY = 'aurastudy_tour_done';
    var TOUR_LANDING_PHASE_KEY = 'aurastudy_tour_landing_phase';

    var overlay = null;
    var spotlight = null;
    var card = null;
    var arrow = null;
    var skipHint = null;
    var activeStep = -1;
    var steps = [];
    var tourRunning = false;
    var onTargetClick = null;
    var keyHandler = null;
    var outsideHandler = null;

    function tourDone() {
        try { localStorage.setItem(TOUR_DONE_KEY, '1'); } catch (e) { /* ignore */ }
        teardown();
    }

    function isTourDone() {
        try { return localStorage.getItem(TOUR_DONE_KEY) === '1'; } catch (e) { return false; }
    }

    function landingPhaseDone() {
        try { localStorage.setItem(TOUR_LANDING_PHASE_KEY, '1'); } catch (e) { /* ignore */ }
    }

    function isLandingPhaseDone() {
        try { return localStorage.getItem(TOUR_LANDING_PHASE_KEY) === '1'; } catch (e) { return false; }
    }

    function readPersistedState() {
        try {
            var raw = localStorage.getItem('aurastudy_state_v1') || localStorage.getItem('aurastudy_girly_v8');
            if (!raw) return null;
            return JSON.parse(raw);
        } catch (e) { return null; }
    }

    function hasExistingStudyData(appState) {
        if (appState && Array.isArray(appState.sessions) && appState.sessions.length > 0) return true;
        var persisted = readPersistedState();
        if (persisted && Array.isArray(persisted.sessions) && persisted.sessions.length > 0) return true;
        try {
            if (localStorage.getItem('aurastudy_running_timer_v1')) return true;
            if (localStorage.getItem('aurastudy_seen_unlocks')) return true;
            if (localStorage.getItem('aurastudy_active_view')) return true;
        } catch (e) { /* ignore */ }
        return false;
    }

    function markTourDoneIfReturningUser(appState) {
        if (isTourDone()) return;
        if (hasExistingStudyData(appState)) tourDone();
    }

    function shouldPlayTour(appState) {
        if (isTourDone()) return false;
        return !hasExistingStudyData(appState);
    }

    function prefersReducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    function ensureDom() {
        if (overlay) return;
        overlay = document.createElement('div');
        overlay.id = 'aura-tour-overlay';
        overlay.setAttribute('aria-hidden', 'true');

        spotlight = document.createElement('div');
        spotlight.className = 'aura-tour-spotlight';
        spotlight.id = 'aura-tour-spotlight';

        arrow = document.createElement('div');
        arrow.className = 'aura-tour-arrow';
        arrow.setAttribute('aria-hidden', 'true');
        arrow.textContent = '👆';

        card = document.createElement('div');
        card.className = 'aura-tour-card';
        card.setAttribute('role', 'dialog');
        card.setAttribute('aria-live', 'polite');
        card.innerHTML =
            '<p class="aura-tour-card-title"></p>' +
            '<p class="aura-tour-card-body"></p>' +
            '<div class="aura-tour-card-actions">' +
            '<button type="button" class="aura-tour-btn aura-tour-btn-ghost" data-tour-action="prev">Back</button>' +
            '<button type="button" class="aura-tour-btn aura-tour-btn-primary" data-tour-action="next">Next</button>' +
            '</div>';

        skipHint = document.createElement('div');
        skipHint.className = 'aura-tour-skip-hint';
        skipHint.textContent = 'Press Esc to skip the tour';

        overlay.appendChild(spotlight);
        overlay.appendChild(arrow);
        overlay.appendChild(card);
        overlay.appendChild(skipHint);
        document.body.appendChild(overlay);

        card.querySelector('[data-tour-action="next"]').addEventListener('click', function () {
            advance(1);
        });
        card.querySelector('[data-tour-action="prev"]').addEventListener('click', function () {
            advance(-1);
        });
    }

    function teardown() {
        tourRunning = false;
        if (keyHandler) {
            document.removeEventListener('keydown', keyHandler, true);
            keyHandler = null;
        }
        if (outsideHandler) {
            document.removeEventListener('click', outsideHandler, true);
            outsideHandler = null;
        }
        if (onTargetClick) {
            onTargetClick.el.removeEventListener('click', onTargetClick.fn, true);
            onTargetClick = null;
        }
        document.querySelectorAll('.aura-tour-target-active').forEach(function (el) {
            el.classList.remove('aura-tour-target-active');
        });
        if (overlay) {
            overlay.hidden = true;
            overlay.setAttribute('aria-hidden', 'true');
        }
        activeStep = -1;
        steps = [];
    }

    function resolveTarget(step) {
        if (!step) return null;
        if (typeof step.target === 'function') return step.target();
        if (typeof step.target === 'string') return document.querySelector(step.target);
        return step.target || null;
    }

    function positionUi(target, step) {
        if (!target || !spotlight || !arrow || !card) return;
        var rect = target.getBoundingClientRect();
        var pad = step.pad || 8;
        var top = Math.max(8, rect.top - pad);
        var left = Math.max(8, rect.left - pad);
        var width = rect.width + pad * 2;
        var height = rect.height + pad * 2;

        spotlight.style.top = top + 'px';
        spotlight.style.left = left + 'px';
        spotlight.style.width = width + 'px';
        spotlight.style.height = height + 'px';

        var cardTop = top + height + 16;
        if (cardTop + card.offsetHeight > window.innerHeight - 12) {
            cardTop = Math.max(12, top - card.offsetHeight - 16);
        }
        var cardLeft = Math.min(Math.max(12, left), window.innerWidth - card.offsetWidth - 12);
        card.style.top = cardTop + 'px';
        card.style.left = cardLeft + 'px';

        var arrowTop = top + height + 4;
        var arrowLeft = left + width / 2 - 14;
        if (cardTop < top) {
            arrowTop = top - 28;
            arrow.textContent = '👇';
        } else {
            arrow.textContent = '👆';
        }
        arrow.style.top = arrowTop + 'px';
        arrow.style.left = Math.min(Math.max(12, arrowLeft), window.innerWidth - 40) + 'px';
    }

    function runBeforeShow(step) {
        if (!step.beforeShow) return Promise.resolve(true);
        try {
            var result = step.beforeShow();
            if (result && typeof result.then === 'function') return result;
            return Promise.resolve(result !== false);
        } catch (e) {
            return Promise.resolve(false);
        }
    }

    function paintStep(index, target, step) {
        target.classList.add('aura-tour-target-active');
        if (step.scroll !== false) {
            try { target.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' }); } catch (e) { /* ignore */ }
        }

        card.querySelector('.aura-tour-card-title').textContent = step.title || '';
        card.querySelector('.aura-tour-card-body').textContent = step.body || '';
        var prevBtn = card.querySelector('[data-tour-action="prev"]');
        var nextBtn = card.querySelector('[data-tour-action="next"]');
        prevBtn.hidden = index <= 0;
        nextBtn.textContent = index >= steps.length - 1 ? 'Done' : 'Next';

        overlay.hidden = false;
        overlay.setAttribute('aria-hidden', 'false');

        window.requestAnimationFrame(function () {
            positionUi(target, step);
        });

        if (step.clickTarget !== false) {
            var clickFn = function (ev) {
                ev.stopPropagation();
                advance(1);
            };
            target.addEventListener('click', clickFn, true);
            onTargetClick = { el: target, fn: clickFn };
        }
    }

    function renderStep(index) {
        var step = steps[index];
        if (!step) return;
        document.querySelectorAll('.aura-tour-target-active').forEach(function (el) {
            el.classList.remove('aura-tour-target-active');
        });
        if (onTargetClick) {
            onTargetClick.el.removeEventListener('click', onTargetClick.fn, true);
            onTargetClick = null;
        }

        runBeforeShow(step).then(function (ready) {
            if (!ready) {
                advance(1);
                return;
            }
            var target = resolveTarget(step);
            if (!target) {
                advance(1);
                return;
            }
            paintStep(index, target, step);
        });
    }

    function advance(delta) {
        var next = activeStep + delta;
        if (next >= steps.length) {
            if (steps === LANDING_STEPS) landingPhaseDone();
            tourDone();
            return;
        }
        if (next < 0) next = 0;
        activeStep = next;
        renderStep(activeStep);
    }

    function startTour(stepList) {
        if (isTourDone() || tourRunning || !stepList || !stepList.length) return;
        tourRunning = true;
        ensureDom();
        steps = stepList;
        activeStep = 0;

        keyHandler = function (ev) {
            if (ev.key === 'Escape') {
                ev.preventDefault();
                tourDone();
                return;
            }
            if (ev.key === 'ArrowRight') {
                ev.preventDefault();
                advance(1);
                return;
            }
            if (ev.key === 'ArrowLeft') {
                ev.preventDefault();
                advance(-1);
            }
        };
        document.addEventListener('keydown', keyHandler, true);

        outsideHandler = function (ev) {
            if (card && card.contains(ev.target)) return;
            if (spotlight && ev.target === spotlight) return;
        };
        document.addEventListener('click', outsideHandler, true);

        window.addEventListener('resize', onResize, { passive: true });
        window.addEventListener('scroll', onResize, { passive: true, capture: true });

        renderStep(0);
    }

    function onResize() {
        if (activeStep < 0 || !steps[activeStep]) return;
        var target = resolveTarget(steps[activeStep]);
        if (target) positionUi(target, steps[activeStep]);
    }

    var LANDING_STEPS = [
        {
            target: '.landing-logo',
            title: 'Welcome to AuraStudy',
            body: 'Your focus timer and study tracker — track sessions, grow your pet, and climb the leaderboard.',
            pad: 12
        },
        {
            target: '#guest-btn',
            title: 'Try it free',
            body: 'Continue as guest — no account needed. You can sign up later to sync and appear on the leaderboard.',
            clickTarget: true
        },
        {
            target: '#login-btn',
            title: 'Or sign in',
            body: 'Log in or sign up free to unlock sync, Spotify, and your spot on the study board.',
            clickTarget: true
        }
    ];

    var APP_STEPS = [
        {
            target: '#view-dashboard',
            title: 'Dashboard',
            body: 'Your study overview — daily progress, streaks, charts, and your evolving pet.',
            beforeShow: function () {
                if (typeof switchView === 'function') {
                    switchView('dashboard', document.querySelector('#app-sidebar .nav-item[title="Dashboard"]'));
                }
                return true;
            }
        },
        {
            target: '#nav-item-timer-toggle',
            title: 'Start a session',
            body: 'Open the timer to begin a countdown or stopwatch study block.',
            clickTarget: true
        },
        {
            target: '#timer-target-trigger',
            title: 'Pick your target',
            body: 'Choose which course you are studying. Tap Target to switch courses anytime.',
            beforeShow: function () {
                if (typeof switchView === 'function') {
                    switchView('timer', document.getElementById('nav-item-timer-toggle'));
                }
                return !!document.getElementById('timer-target-trigger');
            },
            clickTarget: true
        },
        {
            target: '#timer-countdown-stepper',
            title: 'Countdown length',
            body: 'Set how long your focus block runs. Adjust minutes before you start — while idle only.',
            beforeShow: function () {
                if (typeof switchView === 'function') {
                    switchView('timer', document.getElementById('nav-item-timer-toggle'));
                }
                var modePromise = Promise.resolve();
                if (typeof changeEngineMode === 'function' && typeof getActiveEngineModeKey === 'function') {
                    if (getActiveEngineModeKey() !== 'countdown') modePromise = Promise.resolve(changeEngineMode('countdown'));
                }
                return modePromise.then(function () {
                    if (typeof syncTimerCountdownStepperVisibility === 'function') {
                        syncTimerCountdownStepperVisibility();
                    }
                    var stepper = document.getElementById('timer-countdown-stepper');
                    if (stepper) stepper.classList.add('visible');
                    return new Promise(function (resolve) {
                        window.requestAnimationFrame(function () {
                            window.requestAnimationFrame(function () {
                                resolve(!!stepper);
                            });
                        });
                    });
                });
            }
        },
        {
            target: '#app-sidebar .nav-item[title="Courses"]',
            title: 'Courses',
            body: 'Add and manage your subjects here. They appear in the timer target picker.',
            clickTarget: true
        },
        {
            target: '#app-sidebar .nav-item[title="Sessions"]',
            title: 'Session log',
            body: 'Every logged block lands here — review what you studied and when.',
            clickTarget: true
        },
        {
            target: '#app-sidebar .nav-item[title="Achievements"]',
            title: 'Achievements',
            body: 'Unlock badges and trophies as you hit study milestones.',
            clickTarget: true
        },
        {
            target: '#nav-item-music-toggle',
            title: 'Music',
            body: 'Connect Spotify for focus playlists while you study.',
            clickTarget: true
        },
        {
            target: '#app-sidebar .nav-item[title="Leaderboard"]',
            title: 'Leaderboard',
            body: 'See how you rank against other studiers — hover rows for study time and pet tier.',
            clickTarget: true
        },
        {
            target: '#nav-item-help-toggle',
            title: 'Help',
            body: 'Ask questions or send feedback directly to the AuraStudy team.',
            clickTarget: true
        },
        {
            target: '#app-sidebar .nav-item[title="Settings"]',
            title: 'Settings',
            body: 'Daily goals, timer defaults, theme, and account preferences live here.',
            clickTarget: true
        }
    ];

    function initLandingTour() {
        if (isTourDone() || isLandingPhaseDone() || hasExistingStudyData()) {
            if (hasExistingStudyData()) markTourDoneIfReturningUser();
            return;
        }
        if (!document.getElementById('letters') && !document.querySelector('.landing-logo')) return;
        window.setTimeout(function () {
            startTour(LANDING_STEPS);
        }, prefersReducedMotion() ? 400 : 1800);
    }

    function initAppTour(appState) {
        markTourDoneIfReturningUser(appState);
        if (!shouldPlayTour(appState)) return;
        if (!document.getElementById('app-sidebar')) return;
        window.setTimeout(function () {
            startTour(APP_STEPS);
        }, 600);
    }

    window.AuraTour = {
        TOUR_DONE_KEY: TOUR_DONE_KEY,
        initLandingTour: initLandingTour,
        initAppTour: initAppTour,
        isTourDone: isTourDone,
        hasExistingStudyData: hasExistingStudyData,
        markTourDoneIfReturningUser: markTourDoneIfReturningUser,
        shouldPlayTour: shouldPlayTour,
        teardown: teardown
    };

})();
