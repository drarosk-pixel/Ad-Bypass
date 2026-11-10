FROM python:3.12-slim

# xvfb lets us run a *headful* Chromium (far better at clearing Cloudflare than
# plain headless). ca-certificates for TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY bot.py resolver.py ./

# HEADLESS=false (default) + xvfb-run => headful browser inside a virtual display.
ENV HEADLESS=false
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1280x800x24", "python", "bot.py"]
