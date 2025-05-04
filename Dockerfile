# Используем Python 3.12
FROM python:3.12-slim

# Не буферизуем вывод в логи
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Сначала ставим зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код, кроме того, что в .dockerignore
COPY . .

# Гарантируем, что директории для логов и кеша существуют
RUN mkdir -p /app/logs /app/app/cache

# Объявляем директории для монтирования
VOLUME ["/app/logs", "/app/app/cache"]

# Точка входа
CMD ["python", "run.py"]

