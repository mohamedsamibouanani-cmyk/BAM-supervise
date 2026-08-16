FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential default-libmysqlclient-dev pkg-config curl ca-certificates gosu && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Package frontend dependencies inside the image so the browser does not depend on external CDNs.
RUN mkdir -p /app/supervision/static/vendor/bootstrap \
    /app/supervision/static/vendor/bootstrap-icons/fonts \
    /app/supervision/static/vendor/chart && \
    curl -fsSL --retry 3 https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css -o /app/supervision/static/vendor/bootstrap/bootstrap.min.css && \
    curl -fsSL --retry 3 https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js -o /app/supervision/static/vendor/bootstrap/bootstrap.bundle.min.js && \
    curl -fsSL --retry 3 https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css -o /app/supervision/static/vendor/bootstrap-icons/bootstrap-icons.min.css && \
    curl -fsSL --retry 3 https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2 -o /app/supervision/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2 && \
    curl -fsSL --retry 3 https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js -o /app/supervision/static/vendor/chart/chart.umd.min.js
# Runtime user: bootstrap tasks start as root only to prepare persistent volumes;
# Gunicorn itself drops permanently to this unprivileged account.
RUN groupadd --gid 10001 bamapp && \
    useradd --uid 10001 --gid bamapp --no-create-home --shell /usr/sbin/nologin bamapp && \
    mkdir -p /app/media /app/staticfiles && \
    chown -R bamapp:bamapp /app/media /app/staticfiles
# Normalize Windows CRLF line endings so the Linux container can execute the shell script.
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
