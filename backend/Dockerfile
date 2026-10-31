FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY universe.json ./universe.json
COPY frontend/dist ./frontend/dist
ENV PYTHONPATH=/app/backend PYTHONUNBUFFERED=1 DATABASE_URL=sqlite:////app/data/marketlens.db
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "marketlens.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
