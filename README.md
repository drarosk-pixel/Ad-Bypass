# Telegram Final-Link Resolver

Telegram bot jo `arolinks.com` short links ke HTTP redirects, page metadata, aur normal browser flow ko follow karke final public URL deta hai.

## Features

- Fast HTTP redirect resolution, phir zarurat par headless Chromium fallback
- AroLinks-style **Get Link / Continue** buttons aur JSON redirect responses ka support
- SSRF protection: private/local IP addresses aur non-standard ports block hote hain
- Default allowlist sirf `arolinks.com` ke liye
- Optional Telegram user-ID allowlist
- CAPTCHA detection; bot CAPTCHA solve ya bypass nahi karta

## Bot token

1. Telegram mein `@BotFather` kholein.
2. `/newbot` bhejein aur token copy karein.
3. `.env.example` ko `.env` naam se copy karke token set karein.

```bash
cp .env.example .env
```

## Local setup

Python 3.11+ chahiye.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
python bot.py
```

Bot ko Telegram par `/start` bhejein, phir link paste karein:

```text
https://arolinks.com/jFHPD
```

## Docker

```bash
docker build -t telegram-link-resolver .
docker run --rm --env-file .env telegram-link-resolver
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | required | BotFather token |
| `ALLOWED_HOSTS` | `arolinks.com,www.arolinks.com` | Accepted shortener hosts |
| `ALLOWED_USER_IDS` | empty | Comma-separated Telegram IDs; empty allows everyone |
| `RESOLVE_TIMEOUT_SECONDS` | `50` | Per-link browser timeout |
| `MAX_CONCURRENT_RESOLUTIONS` | `2` | Concurrent browser jobs |

Public deployment mein `ALLOWED_USER_IDS` set karna recommended hai, kyunki har resolution browser resources use kar sakta hai.

## Limits

Ad-shortener HTML aur selectors kabhi bhi badal sakte hain. Agar AroLinks apna flow badalta hai, `resolver.py` ke selector list ko update karna padega. CAPTCHA ya human verification wali links automatically resolve nahi hongi.
