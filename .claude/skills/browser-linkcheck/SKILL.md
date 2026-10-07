---
name: browser-linkcheck
description: Verify a link that refuses plain HTTP clients by loading it once in headless Chromium with a stock Chrome user agent. Use during dependency sweeps when curl or urllib gets a 403, 429, timeout, or challenge page and you need to judge whether the link is alive for a human reader.
---

# Browser link check

`probe.js` loads each URL once in Playwright Chromium with a normal Chrome
user agent string (no HeadlessChrome marker), waits for the page to settle,
and prints one JSON line per URL: HTTP status, final URL after redirects,
page title, a short text excerpt, and whether the result looks like a bot
challenge page rather than content.

## Setup (Claude Code cloud sessions)

Playwright and Chromium are preinstalled (PLAYWRIGHT_BROWSERS_PATH is set).
The session's TLS-intercepting proxy re-signs every page, so its CA bundle
must be in Chromium's NSS trust store or every load fails with
ERR_CERT_AUTHORITY_INVALID. One-time fix per session:

    apt-get install -y libnss3-tools
    csplit -z -f /tmp/ca- -b '%02d.crt' /root/.ccr/ca-bundle.crt '/BEGIN CERTIFICATE/' '{*}'
    for c in /tmp/ca-*.crt; do certutil -A -d sql:$HOME/.pki/nssdb -n "ccr-$(basename $c)" -t "C,," -i "$c"; done

Never pass --ignore-certificate-errors instead; the session rules forbid
disabling TLS verification.

## Run

    NODE_PATH=$(npm root -g) node .claude/skills/browser-linkcheck/probe.js URL [URL ...]

## Interpretation

- Status 200 with a real title and excerpt: the link is alive for a human;
  record it as confirmed.
- "Just a moment", "Robot Challenge Screen", or looksLikeChallenge true: the
  site is up but gates automated clients behind a JavaScript or cookie
  challenge this probe does not try to pass. Treat as alive but unconfirmed;
  fall back to a Wayback snapshot or flag it for a manual look.
- Certificate errors: do the setup above. Network errors or proxy 403s: the
  host may be blocked by the session's egress policy; report, do not retry.

## Rules

- One fetch per URL, no retries, no bypassing challenges or rate limits. The
  point is to see what a reader's browser would get, not to defeat access
  controls.
- This is session tooling for link verification only. It is JavaScript and
  uses Playwright; the repo's stdlib-only rule covers the data pipeline, and
  nothing here ships to the published site or the scheduled workflows.
