/**
 * Playwright smoke test: landing book-open animation runs on every reload.
 * Usage: node scripts/verify_landing_animation.js [baseUrl]
 */
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:5055";

async function measureMidFlight(page) {
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

  return { firstLoad: snapshot, secondReload: second };
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
