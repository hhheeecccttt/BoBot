FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py db.py ./

# -u = unbuffered logs so `fly logs` shows output immediately
CMD ["python", "-u", "bot.py"]
