# One image, two roles: API (default CMD) and worker (`python -m app.cli worker`).
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# No system packages are needed.
# Optional build secret `extra_ca` supports networks with a TLS-inspecting proxy:
#   docker build --secret id=extra_ca,src=ca.crt ...  (not stored in the image)
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=extra_ca,required=false,mode=0444 \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements.txt
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY app ./app
RUN useradd --uid 10001 --create-home abp && mkdir -p /app/var && chown -R abp /app/var
USER abp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health/live', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "2"]
