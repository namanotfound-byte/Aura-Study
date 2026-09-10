import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:5055';

async function openTimer(page) {
  await page.goto(`${BASE}/app?guest=1`, { waitUntil: 'networkidle' });
  await page.click('#nav-item-timer-toggle');
  await page.waitForSelector('#timer-display', { state: 'visible', timeout: 15000 });
}

async function sessionCount(page) {
  return page.evaluate(() => (window.appState && Array.isArray(window.appState.sessions)) ? window.appState.sessions.length : -1);
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const results = [];

  try {
    await openTimer(page);

    // --- Stopwatch test ---
    await page.click('#mode-tab-stopwatch');
    await page.click('#timer-start-btn');
    await page.waitForTimeout(6500);
    const beforeLog = await page.locator('#timer-display').innerText();
    const sessionsBefore = await sessionCount(page);
    await page.click('#timer-btn-log');
    await page.waitForTimeout(800);
    const afterLog = await page.locator('#timer-display').innerText();
    const sessionsAfterFirst = await sessionCount(page);
    const logDisabledAfterFirst = await page.locator('#timer-btn-log').isDisabled();
    await page.evaluate(() => { if (typeof saveEngineWorkspaceBlockData === 'function') saveEngineWorkspaceBlockData(); });
    await page.waitForTimeout(300);
    const sessionsAfterSecond = await sessionCount(page);

    results.push({
      test: 'stopwatch',
      beforeLog,
      afterLog,
      displayIsZero: afterLog === '00:00',
      sessionsBefore,
      sessionsAfterFirst,
      sessionsAfterSecond,
      logDisabledAfterFirst,
      secondLogBlocked: sessionsAfterSecond === sessionsAfterFirst,
    });

    // --- Countdown test (fresh page so log lock from stopwatch cannot interfere) ---
    const countdownPage = await context.newPage();
    await openTimer(countdownPage);
    await countdownPage.click('#mode-tab-countdown');
    await countdownPage.waitForTimeout(200);
    await countdownPage.click('#timer-start-btn');
    await countdownPage.waitForTimeout(6500);
    const countdownBeforeLog = await countdownPage.locator('#timer-display').innerText();
    const countdownLogEnabled = !(await countdownPage.locator('#timer-btn-log').isDisabled());
    await countdownPage.click('#timer-btn-log');
    await countdownPage.waitForTimeout(800);
    const countdownAfterLog = await countdownPage.locator('#timer-display').innerText();
    const countdownDebug = await countdownPage.evaluate(() => ({
      mode: window.appState?.selectedMode,
      remaining: typeof countdownSecondsRemainingRegister !== 'undefined' ? countdownSecondsRemainingRegister : null,
      running: typeof runningAccumulatedSeconds !== 'undefined' ? runningAccumulatedSeconds : null,
      loggedLock: typeof engineBlockLoggedThisSession !== 'undefined' ? engineBlockLoggedThisSession : null,
      sessions: window.appState?.sessions?.length ?? null,
    }));

    results.push({
      test: 'countdown',
      beforeLog: countdownBeforeLog,
      afterLog: countdownAfterLog,
      logEnabledBeforeClick: countdownLogEnabled,
      displayIsConfigured: countdownAfterLog === '25:00',
      debug: countdownDebug,
    });
    await countdownPage.close();

    console.log(JSON.stringify({ ok: true, results }, null, 2));

    const stopwatchOk = results[0].displayIsZero && results[0].secondLogBlocked && results[0].sessionsAfterFirst > results[0].sessionsBefore;
    const countdownOk = results[1].displayIsConfigured;
    if (!stopwatchOk || !countdownOk) {
      process.exitCode = 1;
    }
  } catch (err) {
    console.error(JSON.stringify({ ok: false, error: String(err) }, null, 2));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

run();
