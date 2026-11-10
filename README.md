# AroLinks Final-Link Bot

Telegram bot jo `arolinks.com` short link ka **final destination** nikaalta hai.

## Kaise kaam karta hai

AroLinks ek AdLinkFly-based shortener hai jo **Cloudflare** ke peeche hai. Go-link
page to khulta hai, par `/links/go` par Cloudflare ka "managed challenge" aata
hai jo plain HTTP clients (requests/cloudscraper) **clear nahi kar paate**.

Isliye yeh bot ek **asli browser** (Playwright + headful Chromium, Xvfb ke andar)
chalata hai:

1. Short link kholta hai; Cloudflare ka JS challenge browser khud clear karta hai
2. Countdown ka intezaar karta hai
3. "Get Link"/"Continue" button click karta hai
4. Page ki apni JS `/links/go` call karti hai; bot us response se **final URL**
   capture kar leta hai (ya revealed anchor / off-site redirect se)

> Approach un bypassers jaisa hai jo headful browser ko Xvfb me chalate hain,
> kyunki plain headless ko Cloudflare pakad leta hai
> (ref: https://github.com/nekodu/linkgate-bypass).

## Bot token

```bash
cp .env.example .env     # phir TELEGRAM_BOT_TOKEN daalein
```

## Docker (recommended)

```bash
docker build -t arolinks-bot .
docker run --rm --env-file .env arolinks-bot
```

Docker image Chromium + Xvfb set up karta hai; headful browser virtual display
me chalta hai.

## Local run (Linux + display/xvfb)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
xvfb-run -a python bot.py       # headful under xvfb
```

Display na ho to `.env` me `HEADLESS=true` set karein (Cloudflare ke against
kam effective).

## Commands

| Command | Kaam |
|---|---|
| `/start` | Intro |
| `/debug <link>` | Step-by-step resolve report (fail hone par bhejein) |
| `<arolinks link>` | Final link nikaalta hai |

## Config

| Variable | Default | Kaam |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | required | BotFather token |
| `ALLOWED_USER_IDS` | empty | Comma-separated Telegram IDs; empty = sabko allowed |
| `RESOLVE_TIMEOUT_SECONDS` | `90` | Resolve timeout |
| `HEADLESS` | `false` | `true` only if no display/xvfb |

## Zaroori baatein

- Agar Cloudflare **interactive** challenge (checkbox/Turnstile) laga de, to
  headless server se wo solve nahi hoga — bot us page ka link de dega, aap khud
  kholkar solve karein. CAPTCHA bot solve nahi karta.
- AdLinkFly/Cloudflare kabhi bhi apna flow badal sakte hain; aisa hua to
  `resolver.py` ke selectors / logic update karne padenge.
- Sirf `arolinks.com` ke liye.
