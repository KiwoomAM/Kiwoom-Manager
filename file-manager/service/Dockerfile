FROM python:3.11-slim

WORKDIR /app

# ─── SSL 인증서 (사내 프록시 환경) ─────────────────────────────────────
COPY certs/ePrism.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# ─── file-handler 패키지 설치 (zero deps, 로컬 소스에서 빌드) ──────────
COPY packages/file-handler /tmp/file-handler
RUN pip install --no-cache-dir /tmp/file-handler && rm -rf /tmp/file-handler

# ─── mdb-handler SDK 설치 (로컬 소스에서 빌드) ─────────────────────────
COPY packages/mdb-handler/sdk /tmp/mdb-handler-sdk
RUN pip install --no-cache-dir /tmp/mdb-handler-sdk && rm -rf /tmp/mdb-handler-sdk

# ─── FM Service 의존성 ─────────────────────────────────────────────────
COPY packages/file-manager/service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── FM Service 코드 ───────────────────────────────────────────────────
COPY packages/file-manager/service/app/ ./app/

EXPOSE 8600
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8600"]
