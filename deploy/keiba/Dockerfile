# keiba（競馬データ収集サービス）の Cloud Run 用イメージ。
# ビルドコンテキストはリポジトリ root（src/ を含めるため）。
#   docker build -f deploy/keiba/Dockerfile -t <image> .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 日本語フォント（将来の可視化PNGで豆腐を防ぐ）。スクレイピング自体には不要だが軽量。
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存だけ先に入れてレイヤキャッシュを効かせる
COPY requirements-keiba.txt .
RUN pip install -r requirements-keiba.txt

# パッケージ本体
COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV PORT=8080
EXPOSE 8080

# 本番は gunicorn。スクレイピングはレート制限で待つので timeout を長めに。
# workers=2: 同時取得しすぎないよう控えめ（netkeiba への配慮）。
CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 600 \
    keiba.web.app:app
