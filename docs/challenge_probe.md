# ChatGPT Challenge Probe

## Purpose

This helper validates whether a proxy/browser path can reach `https://chatgpt.com/`
without remaining on a Cloudflare/Turnstile challenge page. It is intentionally
kept outside the main automation flow until the evidence is stable enough to wire
into account registration/payment sessions.

## Start FlareSolverr

```powershell
docker run -d --name=flaresolverr-chatgpt-probe -p 8191:8191 -e LOG_LEVEL=info -e TZ=Asia/Tokyo ghcr.io/flaresolverr/flaresolverr:latest
```

Check the service:

```powershell
$body = @{ cmd = 'sessions.list' } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri http://127.0.0.1:8191/v1 -Method Post -Body $body -ContentType 'application/json'
```

## Run Probe

FlareSolverr plus Playwright cookie replay verification:

```powershell
python scripts\chatgpt_challenge_probe.py --mode flaresolverr --proxy-preset flow2-jp --max-proxy-attempts 1 --timeout-seconds 120 --flare-wait-seconds 8 --verify-flaresolverr-with-playwright --output-dir output\challenge_probe --headless
```

Pure Playwright proxy rotation:

```powershell
python scripts\chatgpt_challenge_probe.py --mode playwright --proxy-preset flow2-jp --max-proxy-attempts 2 --timeout-seconds 60 --max-clicks 2 --click-interval-seconds 14 --output-dir output\challenge_probe
```

## Evidence

Each run writes a timestamped folder under `output/challenge_probe/`:

- `summary.json`: one row per attempt.
- `result.json`: FlareSolverr raw response and normalized summary.
- `cookies.json`: cookies returned by FlareSolverr or Playwright.
- `playwright_verify/state.json`: replay verification state when `--verify-flaresolverr-with-playwright` is enabled.
- `page_sample.html` and `screenshot.png`: page evidence for failed Playwright attempts.

Treat a full success as:

- FlareSolverr attempt status is `success`.
- Playwright replay attempt status is `success`.
- `playwright_verify/state.json` has `cloudflare.present=false` and `challengeText=false`.
- The replay page text contains normal ChatGPT landing/login content.
