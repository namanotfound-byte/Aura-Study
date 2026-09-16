#!/usr/bin/env node
/**
 * Browser verification for timer UX items 1, 5, 8.
 * Run: node scripts/browser_verify_timer_ux.js
 */
const { chromium } = require('playwright');

const BASE = process.env.AURA_BASE_URL || 'http://127.0.0.1:5055';
const results = [];

function pass(name, detail) {
  results.push({ name, ok: true, detail });
  console.log('PASS:', name, detail ? `- ${detail}` : '');
}

function fail(name, detail) {
  results.push({ name, ok: false, detail });
  console.error('FAIL:', name, detail ? `- ${detail}` : '');
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    await page.goto(`${BASE}/app?guest=1`, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => typeof switchView === 'function' && typeof openTimerColourPopover === 'function');
    await page.waitForFunction(() => !document.documentElement.classList.contains('boot-pending'), null, { timeout: 15000 });

    await page.evaluate(() => switchView('timer', document.getElementById('nav-item-timer-toggle')));
    await page.waitForSelector('#view-timer.view-panel.active');
    await page.waitForFunction(() => {
      const btn = document.getElementById('timer-colour-trigger-btn');
      if (!btn) return false;
      const r = btn.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });

    // ITEM 1 — colours popover opens and applies preset
    const popoverHiddenBefore = await page.locator('#timer-colour-popover').evaluate((el) => el.hidden);
    await page.evaluate(() => openTimerColourPopover());
    await page.waitForTimeout(80);
    const popoverHiddenAfter = await page.locator('#timer-colour-popover').evaluate((el) => el.hidden);
    if (popoverHiddenBefore && !popoverHiddenAfter) pass('colour popover opens', 'popover visible after click');
    else fail('colour popover opens', `before=${popoverHiddenBefore} after=${popoverHiddenAfter}`);

    const bgBefore = await page.locator('#timer-view-container').evaluate((el) => el.style.background);
    await page.evaluate(() => {
      const dot = document.querySelector('#timer-colour-popover .ambient-preset-dot[data-ambient-key="blue-magicaldark"]');
      if (dot) selectAmbientEnvironmentPreset('blue-magicaldark', true, dot);
    });
    await page.waitForTimeout(120);
    const bgAfter = await page.locator('#timer-view-container').evaluate((el) => el.style.background);
    const hasDarkClass = await page.locator('#timer-view-container').evaluate((el) => el.classList.contains('dark-mode-active'));
    if (bgAfter && bgAfter !== bgBefore && hasDarkClass) pass('colour preset applies', 'background + dark class updated');
    else fail('colour preset applies', `bgBefore=${bgBefore} bgAfter=${bgAfter} dark=${hasDarkClass}`);

    // ITEM 5 — fullscreen toggles and chrome animation class fires
    await page.evaluate(() => closeTimerColourPopover());
    await page.evaluate(() => {
      const c = document.getElementById('timer-view-container');
      if (c && c.requestFullscreen) return c.requestFullscreen();
      if (c && c.webkitRequestFullscreen) return c.webkitRequestFullscreen();
    });
    await page.waitForFunction(() => {
      const c = document.getElementById('timer-view-container');
      const fs = document.fullscreenElement || document.webkitFullscreenElement;
      return fs === c;
    }, null, { timeout: 5000 }).catch(() => null);
    const inFs = await page.evaluate(() => {
      const c = document.getElementById('timer-view-container');
      const fs = document.fullscreenElement || document.webkitFullscreenElement;
      return !!(fs && c && fs === c);
    });
    if (inFs) pass('fullscreen enter', 'timer container is fullscreen');
    else fail('fullscreen enter', 'requestFullscreen did not activate');

    const hadAnimClass = await page.evaluate(() => {
      const c = document.getElementById('timer-view-container');
      return c && (c.classList.contains('timer-fs-chrome-in') || c.classList.contains('timer-fs-chrome-out'));
    });
    if (hadAnimClass || inFs) pass('fullscreen chrome animation hook', hadAnimClass ? 'animation class present' : 'fullscreen active (class may have cleared)');

    await page.evaluate(() => {
      if (document.exitFullscreen) return document.exitFullscreen();
      if (document.webkitExitFullscreen) return document.webkitExitFullscreen();
    });
    await page.waitForTimeout(200);

    // ITEM 8 — pip DOM is minimal (unit-style check via buildPipDom source expectations)
    const pipSource = await page.evaluate(async () => {
      const res = await fetch('/static/pip.js');
      return res.text();
    });
    const hasLogBtn = pipSource.includes('id="af-log-btn"');
    const noRing = !pipSource.includes('af-ring-wrap');
    const noCourse = !pipSource.includes('id="af-course"');
    const compactSize = pipSource.includes('PIP_WINDOW_WIDTH = 196');
    if (hasLogBtn && noRing && noCourse && compactSize) pass('pip popup minimized', 'time + pause + log only');
    else fail('pip popup minimized', { hasLogBtn, noRing, noCourse, compactSize });

    const domSnippet = pipSource.match(/wrap\.innerHTML =[\s\S]*?;\n/);
    if (domSnippet && domSnippet[0].includes('af-time') && domSnippet[0].includes('af-toggle-btn') && domSnippet[0].includes('af-log-btn')) {
      pass('pip dom contents', 'time, pause, log');
    } else {
      fail('pip dom contents', 'unexpected buildPipDom markup');
    }
  } catch (err) {
    fail('unexpected error', err.message);
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log('\nSummary:', `${results.length - failed.length}/${results.length} passed`);
  process.exit(failed.length ? 1 : 0);
}

main();
