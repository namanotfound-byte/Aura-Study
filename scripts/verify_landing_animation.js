/**
 * Playwright smoke test: landing book-open animation runs on every reload.
 * Usage: node scripts/verify_landing_animation.js [baseUrl]
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:5055";

async function verifyCoverClockMidFade(page) {
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() =>
    document.documentElement.classList.contains("landing-animate")
  );
  await page.waitForTimeout(400);

  const clock = await page.evaluate(() => {
    const logo = document.querySelector(".landing-book-cover-logo");
    if (!logo) return { ok: false, reason: "missing cover logo img" };
    const logoStyle = getComputedStyle(logo);
    const logoOpacity = parseFloat(logoStyle.opacity);
    const logoWidth = logo.getBoundingClientRect().width;
    return {
      ok: logoOpacity > 0 && logoWidth > 80,
      logoOpacity,
      logoWidth,
    };
  });

  if (!clock.ok) {
    throw new Error(
      `Cover clock not visible mid-fade at ~400ms: ${JSON.stringify(clock)}`
    );
  }

  await page.waitForTimeout(1800);
  const holdShot = path.join(__dirname, "landing_clock_hold.png");
  await page.screenshot({ path: holdShot, fullPage: false });

  const holdCheck = await page.evaluate(() => {
    const logo = document.querySelector(".landing-book-cover-logo");
    if (!logo) return { ok: false, reason: "missing cover logo img" };
    const logoStyle = getComputedStyle(logo);
    const logoOpacity = parseFloat(logoStyle.opacity);
    const logoWidth = logo.getBoundingClientRect().width;
    const coverFront = document.querySelector(".landing-book-cover-front");
    const coverStyle = coverFront ? getComputedStyle(coverFront) : null;
    return {
      ok: logoOpacity >= 0.95 && logoWidth > 80,
      logoOpacity,
      logoWidth,
      coverBg: coverStyle ? coverStyle.backgroundImage : null,
    };
  });

  if (!holdCheck.ok) {
    throw new Error(
      `Cover clock not fully visible during hold: ${JSON.stringify(holdCheck)}`
    );
  }

  return { midFade: clock, hold: holdCheck, screenshot: holdShot };
}

async function measureMidFlight(page) {
  const clock = await verifyCoverClockMidFade(page);
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(900);

  const snapshot = await page.evaluate(() => {
    const pages = Array.from(document.querySelectorAll(".landing-book-page"));
    const anims = pages.flatMap((el) =>
      el.getAnimations().map((a) => ({
        name: a.animationName,
        currentTime: a.currentTime,
        playState: a.playState,
      }))
    );
    const root = document.documentElement;
    return {
      skipAnim: root.classList.contains("skip-anim"),
      landingAnimate: root.classList.contains("landing-animate"),
      landingSeen: (() => {
        try {
          return window.sessionStorage.getItem("aurastudy:landingSeen");
        } catch (e) {
          return null;
        }
      })(),
      animations: anims,
    };
  });

  const midFlight = snapshot.animations.some(
    (a) =>
      (a.name === "landingBookCoverOpen" ||
        a.name === "landingBookPageSettle" ||
        a.name === "landingBookLift") &&
      a.currentTime != null &&
      a.currentTime > 80 &&
      a.currentTime < 3100
  );

  if (snapshot.skipAnim) {
    throw new Error("First load had skip-anim — animation was skipped");
  }
  if (!snapshot.landingAnimate) {
    throw new Error("First load missing landing-animate class");
  }
  if (!midFlight) {
    throw new Error(
      `No mid-flight book animation on first load: ${JSON.stringify(snapshot)}`
    );
  }

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(900);

  const second = await page.evaluate(() => {
    const pages = Array.from(document.querySelectorAll(".landing-book-page"));
    const anims = pages.flatMap((el) =>
      el.getAnimations().map((a) => ({
        name: a.animationName,
        currentTime: a.currentTime,
        playState: a.playState,
      }))
    );
    const root = document.documentElement;
    return {
      skipAnim: root.classList.contains("skip-anim"),
      landingAnimate: root.classList.contains("landing-animate"),
      animations: anims,
    };
  });

  const secondMidFlight = second.animations.some(
    (a) =>
      (a.name === "landingBookCoverOpen" ||
        a.name === "landingBookPageSettle" ||
        a.name === "landingBookLift") &&
      a.currentTime != null &&
      a.currentTime > 80 &&
      a.currentTime < 3100
  );

  if (second.skipAnim) {
    throw new Error("Second reload had skip-anim — revisit should still animate");
  }
  if (!second.landingAnimate) {
    throw new Error("Second reload missing landing-animate class");
  }
  if (!secondMidFlight) {
    throw new Error(
      `No mid-flight book animation on second reload: ${JSON.stringify(second)}`
    );
  }

  return { clock, firstLoad: snapshot, secondReload: second };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  const result = await measureMidFlight(page);
  console.log(JSON.stringify({ ok: true, ...result }, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
