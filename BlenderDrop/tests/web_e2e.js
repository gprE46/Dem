// Drives the real mobile page in Chromium with iPhone emulation.
// node tests/web_e2e.js <url> <file> <screenshot.png> <locale>
const { chromium, devices } = require("playwright");
(async () => {
  const [url, file, shot, locale] = process.argv.slice(2);
  const browser = await chromium.launch();
  const context = await browser.newContext({ ...devices["iPhone 13"], locale: locale || "es-ES" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(url);
  await page.waitForSelector(".connection.is-connected", { timeout: 10000 });
  const conn = await page.textContent("#connText");
  await page.setInputFiles("#fileInput", file);
  const states = new Set();
  const start = Date.now();
  let final = "";
  while (Date.now() - start < 30000) {
    const s = (await page.textContent("#curState")) || "";
    states.add(s.replace(/\d+%/, "N%"));
    if (/✓$/.test(s) && !/Subido|Uploaded/.test(s)) { final = s; break; }
    if (/Error|failed/.test(s)) { final = s; break; }
    await page.waitForTimeout(50);
  }
  await page.waitForTimeout(3500); // let the recent list refresh
  const recent = await page.$$eval("#recentList li", (lis) => lis.map((li) => li.textContent));
  await page.screenshot({ path: shot, fullPage: true });
  // wrong token page
  const bad = await context.newPage();
  await bad.goto(url.replace(/token=[^&]+/, "token=wrong"));
  await bad.waitForSelector(".connection.is-error", { timeout: 10000 });
  const badText = await bad.textContent("#connInfo");
  console.log(JSON.stringify({ conn, final, states: [...states], recent, badText, errors }));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
