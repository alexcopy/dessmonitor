FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off

WORKDIR /app

#gcc нужна если есть wheels‑less пакеты нет – строки apt-get gcc можно смело убрать
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# копируем код (фильтр .dockerignore)
COPY . .

RUN mkdir -p /app/logs /app/app/cache

VOLUME ["/app/logs", "/app/app/cache"]

CMD ["python", "run.py"]
