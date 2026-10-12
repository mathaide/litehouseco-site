# Origin8 Revenue OS: Python (FastAPI) service. No outside AI or hosting service needed.
FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY web/ web/
ENV PORT=8080 DATA_DIR=/data
EXPOSE 8080
CMD ["python", "app/main.py"]
