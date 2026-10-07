// Browser link probe: confirms whether a URL is alive for a human visitor.
// Loads each URL once in headless Chromium with a stock Chrome user agent,
// reports HTTP status, final URL, page title, and whether the result looks
// like a bot challenge rather than content. One fetch per URL, no retries.
const { chromium } = require('playwright');

const urls = process.argv.slice(2);
if (!urls.length) { console.error('usage: node probe.js URL [URL ...]'); process.exit(2); }

(async () => {
  const browser = await chromium.launch({
    headless: true,
    proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined,
  });
  const ver = browser.version().split('.')[0];
  const ua = `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${browser.version()} Safari/537.36`;
  for (const url of urls) {
    const ctx = await browser.newContext({ userAgent: ua, locale: 'en-US', timezoneId: 'America/Los_Angeles' });
    const page = await ctx.newPage();
    const out = { url };
    try {
      const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      // give JS challenges and client-side redirects a moment to settle
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
      out.status = resp ? resp.status() : null;
      out.final = page.url();
      out.title = (await page.title()).slice(0, 120);
      const text = await page.evaluate(() => document.body ? document.body.innerText.slice(0, 4000) : '');
      out.textBytes = text.length;
      out.excerpt = text.replace(/\s+/g, ' ').slice(0, 180);
      const challenge = /just a moment|verify you are|checking your browser|access denied|attention required|are you a robot|enable javascript and cookies/i;
      out.looksLikeChallenge = challenge.test(out.title) || challenge.test(text.slice(0, 1500));
    } catch (e) {
      out.error = String(e).split('\n')[0].slice(0, 200);
    }
    console.log(JSON.stringify(out));
    await ctx.close();
  }
  await browser.close();
})();
