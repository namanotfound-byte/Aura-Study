/**
 * Measure landing book stage dimensions at closed (~100ms) and settled (~4500ms).
 * Usage: node scripts/measure_landing_book.js [baseUrl] [width] [height]
 */
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:5055";
const VW = Number(process.argv[3]) || 1280;
const VH = Number(process.argv[4]) || 800;

async function measure(page, label, waitMs) {
  await page.waitForTimeout(waitMs);
  return page.evaluate(() => {
    const stage = document.querySelector(".landing-book-stage");
    const cover = document.querySelector(".landing-book-cover");
    const wordmark = document.querySelector(".landing-book-hero-wordmark");
    const actions = document.querySelector(".landing-actions");
    const cs = stage ? getComputedStyle(stage) : null;
    return {
      stageW: cs ? cs.width : null,
      stageH: cs ? cs.height : null,
      coverTransform: cover ? getComputedStyle(cover).transform : null,
      wordmarkOpacity: wordmark ? getComputedStyle(wordmark).opacity : null,
      actionsPosition: actions ? getComputedStyle(actions).position : null,
      actionsVisible: actions ? getComputedStyle(actions).visibility : null,
      coverLogo: !!document.querySelector(".landing-book-cover-logo"),
      coverMark: !!document.querySelector(".landing-book-cover-mark"),
      ctaVisible: (() => {
        const cta = document.querySelector(".cta-row");
        return cta ? getComputedStyle(cta).opacity : null;
      })(),
    };
  });
}

async function runViewport(page, w, h) {
  await page.setViewportSize({ width: w, height: h });
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  const closed = await measure(page, "closed", 100);
  const settled = await measure(page, "settled", 4500);
  return { viewport: `${w}x${h}`, closed, settled };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const desktop = await runViewport(page, VW, VH);
  const mobile = await runViewport(page, 390, 844);

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  const reduced = await page.evaluate(() => {
    const stage = document.querySelector(".landing-book-stage");
    const cs = getComputedStyle(stage);
    return {
      skipAnim: document.documentElement.classList.contains("skip-anim"),
      stageW: cs.width,
      stageH: cs.height,
      actionsPosition: getComputedStyle(document.querySelector(".landing-actions")).position,
      taglineOpacity: getComputedStyle(document.querySelector(".tagline")).opacity,
    };
  });

  console.log(JSON.stringify({ desktop, mobile, reducedMotion: reduced }, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
