FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
LABEL authors="skurbick"

# Устанавливаем рабочую директорию
WORKDIR /app

# Установка шрифтов с поддержкой кириллицы
RUN apt-get update && apt-get install -y \
    fonts-dejavu \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл с зависимостями
COPY requirements.txt constraints.release.txt ./

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt -c constraints.release.txt

# Копируем весь проект
COPY . .

# Открываем порт (внутренний порт контейнера)
EXPOSE 8010

# Запускаем приложение на порту 8010 (внутренний)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]