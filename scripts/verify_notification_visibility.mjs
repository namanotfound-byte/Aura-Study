/**
 * Smoke-check: visible tab must not post TIMER_SHOW; hidden tab may.
 */
import { chromium } from "playwright";

const BASE = process.env.BASE || "http://127.0.0.1:5055";

async function openTimer(page) {
  await page.goto(`${BASE}/app?guest=1`, { waitUntil: "networkidle" });
  await page.click("#nav-item-timer-toggle");
  await page.waitForSelector("#timer-display", { state: "visible", timeout: 15000 });
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  await context.grantPermissions(["notifications"], { origin: new URL(BASE).origin });

  await page.addInitScript(() => {
    window.__swPosts = [];
    const origRegister = navigator.serviceWorker.register.bind(navigator.serviceWorker);
    navigator.serviceWorker.register = function () {
      return origRegister.apply(this, arguments).then(function (reg) {
        function hookWorker(worker) {
          if (!worker || worker.__hooked) return;
          worker.__hooked = true;
          var orig = worker.postMessage.bind(worker);
          worker.postMessage = function (msg) {
            window.__swPosts.push(JSON.parse(JSON.stringify(msg || {})));
            return orig(msg);
          };
        }
        hookWorker(reg.active);
        hookWorker(reg.waiting);
        hookWorker(reg.installing);
        reg.addEventListener("updatefound", function () {
          hookWorker(reg.installing);
        });
        return reg;
      });
    };
  });

  try {
    await openTimer(page);
    await page.waitForFunction(
      () => typeof toggleEngineExecutionLoop === "function" && typeof AuraFocus !== "undefined"
    );
    await page.evaluate(async () => {
      Object.defineProperty(Notification, "permission", {
        configurable: true,
        get: () => "granted",
      });
      AuraFocus.init();
      appState.profile.focusMode.notify = true;
      appState.profile.focusMode.floatTimer = false;
      await changeEngineMode("stopwatch");
      if (!isEngineActivelyRunning) toggleEngineExecutionLoop();
      await navigator.serviceWorker.ready;
    });
    await page.waitForTimeout(1000);

    const visibleCheck = await page.evaluate(async () => {
      Object.defineProperty(Notification, "permission", {
        configurable: true,
        get: () => "granted",
      });
      window.__swPosts = [];
      if (typeof updateEngineDisplayString === "function") updateEngineDisplayString();
      await new Promise((r) => setTimeout(r, 400));
      var showWhileVisible = window.__swPosts.filter((m) => m.type === "TIMER_SHOW").length;
      var clearWhileVisible = window.__swPosts.filter((m) => m.type === "TIMER_CLEAR").length;

      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
      document.dispatchEvent(new Event("visibilitychange"));
      await new Promise((r) => setTimeout(r, 600));
      var showWhileHidden = window.__swPosts.filter((m) => m.type === "TIMER_SHOW").length;

      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
      await new Promise((r) => setTimeout(r, 400));
      var clearAfterReturn = window.__swPosts.filter((m) => m.type === "TIMER_CLEAR").length;
      var resetAfterReturn = window.__swPosts.filter((m) => m.type === "TIMER_RESET_DISMISS").length;

      return {
        showWhileVisible,
        clearWhileVisible,
        showWhileHidden,
        clearAfterReturn,
        resetAfterReturn,
        permission: Notification.permission,
        posts: window.__swPosts,
      };
    });

    const ok =
      visibleCheck.showWhileVisible === 0 &&
      visibleCheck.showWhileHidden >= 1 &&
      visibleCheck.clearAfterReturn >= 1 &&
      visibleCheck.resetAfterReturn >= 1 &&
      visibleCheck.permission === "granted";

    console.log(JSON.stringify({ ok, visibleCheck }, null, 2));
    if (!ok) process.exitCode = 1;
  } catch (err) {
    console.error(JSON.stringify({ ok: false, error: String(err) }, null, 2));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

run();
