import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:5055';

async function openTimer(page) {
  await page.goto(`${BASE}/app?guest=1`, { waitUntil: 'networkidle' });
  await page.click('#nav-item-timer-toggle');
  await page.waitForSelector('#timer-display', { state: 'visible', timeout: 15000 });
}

async function parseDisplay(text) {
  const parts = text.trim().split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return parts[0] * 60 + parts[1];
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ permissions: ['notifications'] });
  const page = await context.newPage();
  const results = {};

  try {
    await openTimer(page);
    await page.waitForFunction(() => typeof toggleEngineExecutionLoop === 'function' && typeof changeEngineMode === 'function');
    await page.evaluate(async () => {
      await changeEngineMode('stopwatch');
      if (!isEngineActivelyRunning) toggleEngineExecutionLoop();
    });
    await page.waitForTimeout(1200);

    const beforeHidden = await page.locator('#timer-display').innerText();
    const anchorBefore = await page.evaluate(() => ({
      anchor: typeof engineAnchorMs !== 'undefined' ? engineAnchorMs : null,
      banked: typeof bankedElapsedSeconds !== 'undefined' ? bankedElapsedSeconds : null,
      running: typeof isEngineActivelyRunning !== 'undefined' ? isEngineActivelyRunning : null,
    }));

    // Simulate sleep/background: jump wall clock forward 65s, then resync.
    await page.evaluate(() => {
      const realNow = Date.now;
      const jumpMs = 65000;
      Date.now = () => realNow() + jumpMs;
      if (typeof syncEngineRegistersFromClock === 'function') syncEngineRegistersFromClock();
      if (typeof updateEngineDisplayString === 'function') updateEngineDisplayString();
    });

    const afterWake = await page.locator('#timer-display').innerText();
    const runningAfter = await page.evaluate(() =>
      typeof runningAccumulatedSeconds !== 'undefined' ? runningAccumulatedSeconds : null
    );
    const secsBefore = parseDisplay(beforeHidden);
    const secsAfter = parseDisplay(afterWake);
    const displayDelta = Number.isFinite(secsBefore) && Number.isFinite(secsAfter) ? secsAfter - secsBefore : null;
    results.wallClockSleep = {
      beforeHidden,
      afterWake,
      runningAccumulatedSeconds: runningAfter,
      deltaSeconds: displayDelta,
      anchorBefore,
      passes: runningAfter >= 60,
    };

    const swInfo = await page.evaluate(async () => {
      if (!('serviceWorker' in navigator)) return { supported: false };
      const reg = await navigator.serviceWorker.getRegistration('/');
      return {
        supported: true,
        registered: !!reg,
        scriptURL: reg?.active?.scriptURL || reg?.installing?.scriptURL || null,
      };
    });
    results.serviceWorker = swInfo;

    const manifestOk = await page.evaluate(async () => {
      const res = await fetch('/manifest.webmanifest');
      if (!res.ok) return false;
      const json = await res.json();
      return json.name === 'AuraStudy' && Array.isArray(json.icons);
    });
    results.manifestOk = manifestOk;

    console.log(JSON.stringify({ ok: true, results }, null, 2));
    const ok = results.wallClockSleep.passes && results.manifestOk;
    if (!ok) process.exitCode = 1;
  } catch (err) {
    console.error(JSON.stringify({ ok: false, error: String(err) }, null, 2));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

run();
