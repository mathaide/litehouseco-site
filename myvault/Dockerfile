# MyVault Lender Intelligence OS: Python service for the hosted demonstration.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py docparser.py lhgate.py ./
COPY static/ static/
COPY data/ data/
ENV PORT=8080 HOSTED=1 STATE_DIR=/state
EXPOSE 8080
CMD ["python", "app.py"]
