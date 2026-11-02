FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright + PDF rendering
RUN apt-get update && apt-get install -y \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes6 \
    libxrandr2 libgbm1 libpango-1.0-0 libpangocairo-1.0-0 \
    libasound2 libgtk-3-0 wget curl xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-service.txt .
RUN pip install --no-cache-dir -r requirements-service.txt

COPY . .

ENV PLAYWRIGHT_BROWSERS_PATH=/data/home/.cache/ms-playwright
ENV ZEROTHEBOT_HOST=0.0.0.0
ENV PORT=8765
ENV INSTABOX_HEADLESS=0
ENV DISPLAY=:1

EXPOSE 8765

CMD ["bash", "run-host.sh"]
