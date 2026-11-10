FROM mcr.microsoft.com/playwright/python:v1.65.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py resolver.py ./

RUN useradd --create-home --uid 10001 botuser \
    && chown -R botuser:botuser /app
USER botuser

CMD ["python", "bot.py"]
