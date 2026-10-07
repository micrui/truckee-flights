// Browser link probe: confirms whether a URL is alive for a human visitor.
// Loads each URL once in headless Chromium presenting as a normal browser,
// waits out automatic bot challenges (Cloudflare and similar) that resolve
// without interaction, and reports status, final URL, title, and whether a
// challenge remained. One fetch per URL, no retries, no CAPTCHA solving and
// no clicking of human-verification checkboxes.
const { chromium } = require('playwright');

const urls = process.argv.slice(2);
if (!urls.length) { console.error('usage: node probe.js URL [URL ...]'); process.exit(2); }

const CHALLENGE = /just a moment|verify you are|checking your browser|checking the site connection|robot challenge|attention required|are you a robot|enable javascript and cookies|performing security verification/i;
const PROFILE = process.env.LINKCHECK_PROFILE
  || (process.env.CLAUDE_SCRATCHPAD_DIR || '/tmp') + '/linkcheck-profile';

(async () => {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    headless: true,
    channel: 'chromium', // full build in new headless mode, not the headless shell
    args: ['--disable-blink-features=AutomationControlled'],
    locale: 'en-US',
    timezoneId: 'America/Los_Angeles',
    viewport: { width: 1366, height: 824 },
    proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined,
  });
  // strip the Headless marker from the real UA
  const page = await ctx.newPage();
  const realUA = await page.evaluate(() => navigator.userAgent);
  await page.close();
  const ua = realUA.replace('HeadlessChrome', 'Chrome');

  for (const url of urls) {
    const p = await ctx.newPage();
    await p.setExtraHTTPHeaders({}); // no-op; UA set below via route not possible, use context option fallback
    const out = { url };
    try {
      // per-page UA override
      const cdp = await ctx.newCDPSession(p);
      await cdp.send('Network.setUserAgentOverride', { userAgent: ua });
      const resp = await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      out.status = resp ? resp.status() : null;
      // wait out an automatic challenge: poll until the page stops looking
      // like one, it navigates, or 30s passes
      let challenged = false;
      for (let waited = 0; waited <= 30000; waited += 2000) {
        const title = await p.title().catch(() => '');
        const text = await p.evaluate(() => document.body ? document.body.innerText.slice(0, 1500) : '').catch(() => '');
        if (!(CHALLENGE.test(title) || CHALLENGE.test(text))) { challenged = waited > 0; break; }
        challenged = true;
        await p.waitForTimeout(2000);
      }
      await p.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
      out.final = p.url();
      out.title = (await p.title()).slice(0, 120);
      const text = await p.evaluate(() => document.body ? document.body.innerText.slice(0, 4000) : '');
      out.textBytes = text.length;
      out.excerpt = text.replace(/\s+/g, ' ').slice(0, 180);
      out.challenged = challenged;
      out.challengeCleared = challenged && !(CHALLENGE.test(out.title) || CHALLENGE.test(text.slice(0, 1500)));
      out.stillChallenge = CHALLENGE.test(out.title) || CHALLENGE.test(text.slice(0, 1500));
    } catch (e) {
      out.error = String(e).split('\n')[0].slice(0, 200);
    }
    console.log(JSON.stringify(out));
    await p.close();
  }
  await ctx.close();
})();
