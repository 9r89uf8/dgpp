# aeroweb-proxy

Cloudflare Worker that proxies requests from Convex to `aviation.meteo.fr` (AEROWEB). This avoids IP bans on Convex's shared serverless infrastructure by routing requests through Cloudflare's distributed edge network.

## Why this exists

AEROWEB (aviation.meteo.fr) rate-limited/IP-banned the Convex server IP after excessive polling. Since Convex runs on shared infrastructure, you can't change its outbound IP. This Worker sits in between:

```
Convex action → Cloudflare Worker (thousands of IPs) → aviation.meteo.fr
```

Cloudflare's IPs are shared with millions of legitimate websites, making IP bans impractical for the upstream.

## How it works

1. Convex sets `AEROWEB_BASE_URL` to this Worker's URL instead of `https://aviation.meteo.fr`
2. All AEROWEB requests (login, METAR fetch) go to the Worker
3. The Worker verifies a shared secret (`x-proxy-secret` header), then forwards the request to `aviation.meteo.fr` with all headers, cookies, and body intact
4. The response (including `Set-Cookie` for session management) is returned to Convex as-is

The existing Convex login/fetch code in `convex/aeroweb.js` doesn't change — it just talks to a different base URL.

## Proxied paths

Only these paths are forwarded (all others return 404):

- `/login.php` — initial page load to get session cookies
- `/ajax/login_valid.php` — POST login with credentials
- `/showmessage.php?code=LFPG` — fetch latest METAR for a station
- `/accueil.php` — home page (used as Referer)
- `/affichemessages.php` — alternative message display
- `/bulletin_maa.php` — MAA bulletins

## Setup

### 1. Deploy the Worker

```bash
cd workers/aeroweb-proxy
npx wrangler deploy
```

This outputs the Worker URL, e.g. `https://aeroweb-proxy.<account>.workers.dev`

### 2. Set the shared secret

```bash
npx wrangler secret put PROXY_SECRET
# Enter a random string, e.g.: aeroweb-proxy-s3cr3t-2026
```

### 3. Configure Convex to use the proxy

```bash
npx convex env set AEROWEB_BASE_URL "https://aeroweb-proxy.<account>.workers.dev"
npx convex env set AEROWEB_PROXY_SECRET "<same-secret-from-step-2>"
```

### 4. Verify

Trigger a manual AEROWEB fetch from the Paris page ("Fetch Official Now" button) or run:

```bash
npx convex run aeroweb:pollLatestStationMetar '{"stationIcao":"LFPG"}'
```

## How to add more airports

The Worker is airport-agnostic — it proxies any request to `aviation.meteo.fr`. To fetch METARs for a different French airport, just call the same Convex action with a different ICAO code:

```bash
npx convex run aeroweb:pollLatestStationMetar '{"stationIcao":"LFPO"}'
```

This hits `/showmessage.php?code=LFPO` through the proxy. No Worker changes needed — any ICAO code that AEROWEB supports will work.

## How to make a proxy for another provider

If another provider (e.g. `ama.aemet.es` for Madrid) gets IP-banned, create a new Worker following the same pattern:

### 1. Create the Worker

```bash
mkdir -p workers/aemet-proxy
cp -r workers/aeroweb-proxy/package.json workers/aemet-proxy/
cp -r workers/aeroweb-proxy/src workers/aemet-proxy/
```

### 2. Update `wrangler.toml`

```toml
name = "aemet-proxy"
main = "src/index.js"
compatibility_date = "2024-12-01"

[vars]
UPSTREAM_BASE = "https://ama.aemet.es"
```

### 3. Update `ALLOWED_PATHS` in `src/index.js`

Change the allowed paths to match the new provider's endpoints:

```js
const ALLOWED_PATHS = [
  "/login.php",
  "/buscador_mensajes.php",
  // ... whatever paths the provider uses
];
```

### 4. Deploy and configure

```bash
cd workers/aemet-proxy
npx wrangler deploy
npx wrangler secret put PROXY_SECRET

# Set Convex env vars for the Madrid module
npx convex env set AEMET_BASE_URL "https://aemet-proxy.<account>.workers.dev"
npx convex env set AEMET_PROXY_SECRET "<secret>"
```

### 5. Update the Convex fetch function

Add the same `x-proxy-secret` header injection pattern used in `convex/aeroweb.js` to the relevant module's `fetchWithTimeout`.

## Convex env vars reference

| Variable | Purpose |
|---|---|
| `AEROWEB_BASE_URL` | Worker URL (replaces `https://aviation.meteo.fr`) |
| `AEROWEB_PROXY_SECRET` | Shared secret sent as `x-proxy-secret` header |
| `AEROWEB_LOGIN` | AEROWEB account username |
| `AEROWEB_PASSWORD` | AEROWEB account password |

## Troubleshooting

**403 from the upstream**: The Worker's Cloudflare IP might also be banned (unlikely but possible). Wait 24-48 hours or deploy to a different Cloudflare region.

**401 from the Worker**: The `x-proxy-secret` header doesn't match `PROXY_SECRET`. Check that `AEROWEB_PROXY_SECRET` in Convex matches what you set via `wrangler secret put`.

**AEROWEB login failed**: The AEROWEB credentials (`AEROWEB_LOGIN` / `AEROWEB_PASSWORD`) are wrong or the account is locked. Try logging in manually at `aviation.meteo.fr`.

**To bypass the proxy temporarily**: Remove or unset `AEROWEB_BASE_URL` in Convex — the code falls back to `https://aviation.meteo.fr` directly.

```bash
npx convex env unset AEROWEB_BASE_URL
npx convex env unset AEROWEB_PROXY_SECRET
```
# dgpp
