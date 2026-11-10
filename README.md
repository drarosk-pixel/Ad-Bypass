# AroLinks Final-Link Bot

Telegram bot jo `arolinks.com` short link ka **final destination** nikaalta hai —
bina browser ke, pure HTTP se.

## Kaise kaam karta hai

AroLinks ek AdLinkFly-based shortener hai. Wo destination seedha nahi dikhata;
pehle ek partner article chain (`techmint.in` → `onlinewish.in`) se guzarna
padta hai. Bot wahi chain follow karta hai, phir `onlinewish.in` referer ke
saath arolinks ko hit karta hai (timer/ad hat jaata hai) aur `form#go-link` ko
`/links/go` par POST karke JSON me asli URL le leta hai.

> Flow public reference se liya gaya hai:
> https://github.com/MeherMankar/FZBypassBot
> (logic reimplemented; content rephrased for licensing compliance.)

## Bot token

1. Telegram me `@BotFather` kholein, `/newbot` bhejein, token copy karein.
2. `.env.example` ko `.env` banayein aur token daalein:

```bash
cp .env.example .env
```

## Local run

Python 3.11+ chahiye.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Bot ko link bhejein:

```text
https://arolinks.com/jFHPD
```

## Docker

```bash
docker build -t arolinks-bot .
docker run --rm --env-file .env arolinks-bot
```

## Config

| Variable | Default | Kaam |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | required | BotFather token |
| `ALLOWED_USER_IDS` | empty | Comma-separated Telegram IDs; empty = sabko allowed |
| `RESOLVE_TIMEOUT_SECONDS` | `60` | Resolve timeout |

## Zaroori baatein

- **CAPTCHA bot solve nahi karta.** Agar kisi link par CAPTCHA aaya, bot us
  page ka link de dega taaki aap khud solve kar sakein. (Normal arolinks flow me
  referer trick CAPTCHA se bacha deta hai.)
- AroLinks / partner sites apna flow ya partner domains kabhi bhi badal sakte
  hain. Aisa hua to `resolver.py` me chain steps / regex update karne padenge.
- Yeh sirf `arolinks.com` ke liye bana hai.
