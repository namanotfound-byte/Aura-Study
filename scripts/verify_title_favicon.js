/**
 * Playwright smoke test: tab title stays "AuraStudy" and favicon loads.
 * Usage: node scripts/verify_title_favicon.js [baseUrl]
 */
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:5055";

async function assertFavicon(page, href) {
  const status = await page.evaluate(async (url) => {
    const res = await fetch(url, { method: "HEAD", credentials: "same-origin" });
    const ct = res.headers.get("content-type") || "";
    return { ok: res.ok, status: res.status, contentType: ct };
  }, href);
  if (!status.ok || !status.contentType.includes("image")) {
    throw new Error(`Favicon ${href} failed: ${JSON.stringify(status)}`);
  }
  return status;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  await page.goto(`${BASE}/app?guest=1`, { waitUntil: "domcontentloaded" });
  let title = await page.title();
  if (title !== "AuraStudy") {
    throw new Error(`Expected title "AuraStudy", got "${title}"`);
  }

  const iconHref = await page.locator('link[rel="icon"]').first().getAttribute("href");
  if (!iconHref) {
    throw new Error("No favicon link found in /app HTML");
  }
  if (!iconHref.includes("favicon-32.png")) {
    throw new Error(`Expected first icon to be favicon-32.png, got ${iconHref}`);
  }
  const faviconStatus = await assertFavicon(page, iconHref);

  const pngResp = await page.request.get(`${BASE}${iconHref.split("?")[0]}`);
  if (!pngResp.ok()) {
    throw new Error(`favicon-32.png returned ${pngResp.status()}`);
  }
  const pngBody = await pngResp.body();
  if (pngBody.length < 500) {
    throw new Error(`favicon-32.png too small (${pngBody.length} bytes)`);
  }

  // Simulate tab hidden while timer prefs would have rewritten title before fix.
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(300);
  title = await page.title();
  if (title !== "AuraStudy") {
    throw new Error(`Title changed on visibilitychange: "${title}"`);
  }

  if (typeof window !== "undefined" && typeof AuraFocus !== "undefined") {
    // noop — AuraFocus may not be on window in Node eval
  }
  await page.evaluate(() => {
    if (window.AuraFocus && typeof window.AuraFocus.init === "function") {
      window.AuraFocus.init();
    }
  });
  title = await page.title();
  if (title !== "AuraStudy") {
    throw new Error(`Title changed after AuraFocus.init(): "${title}"`);
  }

  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  title = await page.title();
  if (title !== "AuraStudy") {
    throw new Error(`Landing title expected "AuraStudy", got "${title}"`);
  }

  const faviconResp = await page.request.get(`${BASE}/favicon.ico`);
  if (!faviconResp.ok()) {
    throw new Error(`/favicon.ico returned ${faviconResp.status()}`);
  }
  const ct = faviconResp.headers()["content-type"] || "";
  if (!ct.includes("image")) {
    throw new Error(`/favicon.ico wrong content-type: ${ct}`);
  }
  const icoBody = await faviconResp.body();
  if (icoBody.length < 2000) {
    throw new Error(`/favicon.ico too small (${icoBody.length} bytes) — expected multi-size ICO`);
  }
  const cc = faviconResp.headers()["cache-control"] || "";
  if (!cc.includes("max-age")) {
    throw new Error(`/favicon.ico missing short Cache-Control, got: ${cc}`);
  }

  const appName = await page.locator('meta[name="application-name"]').getAttribute("content");
  if (appName !== "AuraStudy") {
    throw new Error(`Expected application-name AuraStudy, got "${appName}"`);
  }

  console.log(
    JSON.stringify(
      {
        ok: true,
        appTitle: "AuraStudy",
        iconHref,
        faviconStatus,
        faviconRoute: "/favicon.ico",
        faviconContentType: ct,
      },
      null,
      2
    )
  );

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
