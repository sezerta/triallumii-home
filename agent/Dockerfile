FROM python:3.12-alpine
WORKDIR /app
COPY agent/server.py .
ENV HOST_PROC=/host/proc HOST_ROOT=/host/root PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1:8080/api/health >/dev/null || exit 1
CMD ["python3", "-u", "server.py"]
