FROM python:3.12-slim
LABEL authors="skurbick"

# Устанавливаем рабочую директорию
WORKDIR /app

# Установка шрифтов с поддержкой кириллицы
RUN apt-get update && apt-get install -y \
    fonts-dejavu \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Открываем порт (внутренний порт контейнера)
EXPOSE 8010

# Запускаем приложение на порту 8010 (внутренний)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]