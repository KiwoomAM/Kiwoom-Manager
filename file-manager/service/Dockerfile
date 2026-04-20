FROM python:3.11-slim

WORKDIR /app

# 인증서
COPY certs/ePrism.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# mdb-handler SDK 복사
COPY packages/mdb-handler/sdk/ /deps/mdb-handler-sdk/
RUN pip install --no-cache-dir /deps/mdb-handler-sdk

# service 의존성 설치
RUN pip install --no-cache-dir fastapi uvicorn pydantic-settings python-multipart

# service 코드 복사
COPY packages/file-manager/service/app/ ./app/

EXPOSE 8102
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8102"]
