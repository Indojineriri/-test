FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

# LibreOffice (Impress) is needed to rasterise slides to images.
# fonts-noto-cjk ensures Japanese text renders correctly (no tofu boxes).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Cloud Run injects $PORT (default 8080); Streamlit must bind 0.0.0.0:$PORT.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
