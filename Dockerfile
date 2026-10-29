FROM python:3.12-slim
WORKDIR /srv
RUN useradd --create-home app && mkdir -p /srv/state && chown app /srv/state
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data ./data
USER app
ENV DB_PATH=/srv/state/agent.db
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
