---
name: browser-linkcheck
description: Verify a link that refuses plain HTTP clients by loading it in headless Chromium presenting as a normal browser, waiting out automatic bot challenges. Use during dependency sweeps when curl or urllib gets a 403, 429, timeout, or challenge page and you need to judge whether the link is alive for a human reader.
---

# Browser link check

`probe.js` loads each URL once in Playwright Chromium presenting as a normal
browser: the full Chromium build (not the headless shell), the stock Chrome
user agent with the Headless marker removed, the automation flag
(navigator.webdriver) disabled, and a persistent profile so clearance
cookies carry across URLs in a run. When it lands on an automatic bot
challenge (Cloudflare and similar) it waits up to 30 seconds for the
challenge to resolve itself, then reports. Output is one JSON line per URL:
status, final URL, title, a text excerpt, whether a challenge appeared,
whether it cleared, and whether one remained.

## Setup (Claude Code cloud sessions)

Playwright and Chromium are preinstalled (PLAYWRIGHT_BROWSERS_PATH is set).
The session's TLS-intercepting proxy re-signs every page, so its CA bundle
must be in Chromium's NSS trust store or every load fails with
ERR_CERT_AUTHORITY_INVALID. One-time fix per session:

    apt-get install -y libnss3-tools
    csplit -z -f /tmp/ca- -b '%02d.crt' /root/.ccr/ca-bundle.crt '/BEGIN CERTIFICATE/' '{*}'
    for c in /tmp/ca-*.crt; do certutil -A -d sql:$HOME/.pki/nssdb -n "ccr-$(basename $c)" -t "C,," -i "$c"; done

Never pass --ignore-certificate-errors instead; the session rules forbid
disabling TLS verification. Local sessions need none of this.

## Run

    NODE_PATH=$(npm root -g) node .claude/skills/browser-linkcheck/probe.js URL [URL ...]

## Interpretation

- Status 200 with a real title and excerpt: the link is alive for a human;
  record it as confirmed. challenged true with challengeCleared true means it
  passed a bot check on the way, which is itself evidence the site treats a
  normal browser fine.
- stillChallenge true: the challenge did not auto-clear. In a cloud session
  this is expected for Cloudflare-class checks and says nothing about the
  link: the sandbox egress proxy re-terminates TLS, so the challenge vendor
  sees a non-browser TLS fingerprint on a datacenter address and will not
  auto-pass it however real the browser is. Re-run the probe from a local
  Claude Code session on the maintainer's machine (real fingerprint,
  residential address), where these clear on their own; or fall back to a
  Wayback snapshot; or flag for a manual look. Only a challenge that fails
  locally too is worth reporting as a problem.
- Certificate errors: do the setup above. Network errors or proxy 403s: the
  host may be blocked by the session's egress policy; report, do not retry.

## Rules

- One fetch per URL per run, no retry loops. Waiting out an automatic
  challenge is fine; that is what a reader's browser does. Do not script
  clicks on "verify you are human" checkboxes, do not use CAPTCHA-solving
  services, and do not rotate identities to defeat a block: a site that
  still refuses a clean local browser run has answered the question, and the
  answer goes in the report.
- This is session tooling for link verification only. It is JavaScript and
  uses Playwright; the repo's stdlib-only rule covers the data pipeline, and
  nothing here ships to the published site or the scheduled workflows.

## Recheck queue

A sweep running in a cloud session appends each URL whose challenge did not
clear to data/health/recheck-queue.txt (one URL per line, # for comments)
instead of calling it unverifiable. A local session or the maintainer burns
the queue down: run the probe on those URLs from a residential connection,
record the outcome as a dated addendum in the current data/health report,
and remove the cleared lines. An absent or empty queue means nothing waits.
