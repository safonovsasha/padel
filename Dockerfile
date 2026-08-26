FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY padel_bot.py .
CMD ["python", "padel_bot.py"]
