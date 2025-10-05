# Этап сборки
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем зависимости из builder
COPY --from=builder /root/.local /root/.local

COPY app/ .

# Добавляем .local в PATH
ENV PATH=/root/.local/bin:$PATH

# Открытие портов
EXPOSE 8000

# Команда по умолчанию
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
