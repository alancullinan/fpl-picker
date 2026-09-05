# Claude proxy

The site is public, so the Anthropic API key cannot sit in the browser. This
Worker holds it, verifies the caller is you, and streams Claude's reply back —
which is what makes the Ask box answer in seconds rather than the minutes a
GitHub Actions round trip takes.

## Deploy

From this directory:

```bash
npx wrangler login                       # opens the browser, authorises the CLI
npx wrangler secret put ANTHROPIC_API_KEY   # paste the same key as the repo secret
npx wrangler deploy
```

`deploy` prints a URL like `https://fpl-picker-claude.<your-subdomain>.workers.dev`.
Put it in `firebase-config.js` as `window.FPL_WORKER`, commit, and the Ask box
appears on the Advice tab for anyone signed in.

## Locking it to you

Until `ALLOWED_UIDS` is set, any signed-in Google account that knows the URL can
spend against your key. To close that:

1. Open the site, sign in, go to the Advice tab. Your Firebase uid is shown
   under the Ask box.
2. Put it in `ALLOWED_UIDS` in `wrangler.toml`.
3. `npx wrangler deploy` again.

## What it will and will not do

The model and the token ceiling are fixed in the Worker, so a compromised page
cannot run up a large bill: each answer is capped at 4,000 output tokens, about
2-3p. Requests larger than 400,000 characters are refused. The Worker keeps no
logs of your questions beyond Cloudflare's own request metrics.
