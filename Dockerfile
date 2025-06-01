FROM python:3.9-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Создаем необходимые директории
RUN mkdir -p uploads extracted static/chats templates

# Устанавливаем права на запись для папок
RUN chmod 755 uploads extracted static/chats

# Открываем порт
EXPOSE 5000

# Команда для запуска приложения
CMD ["python", "app.py"]
