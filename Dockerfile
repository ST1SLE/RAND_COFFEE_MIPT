# Используем Python 3.12 Slim (легковесный Linux)
FROM python:3.12-slim

# Установка системных зависимостей (нужны для сборки некоторых python-пакетов)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Установка рабочей директории
WORKDIR /app

# Сначала копируем зависимости (кэширование слоев Docker)
COPY requirements.txt .

# Установка Python библиотек
# --no-cache-dir уменьшает размер образа
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь исходный код
COPY . .

# Указываем, что при запуске Python должен искать модули в текущей папке
ENV PYTHONPATH=/app

# Команда по умолчанию (будет переопределена в docker-compose)
CMD ["python", "src/bot.py"]