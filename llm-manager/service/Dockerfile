FROM python:3.12-slim

WORKDIR /app

# 인증서
COPY certs/ePrism.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# 내부 handler SDK 복사 (llm-request가 의존하는 패키지)
COPY packages/llm-handler/sdk/ /deps/llm-handler-sdk/
COPY packages/mdb-handler/sdk/ /deps/mdb-handler-sdk/
COPY packages/redis-handler/ /deps/redis-handler/
COPY packages/file-manager/sdk/ /deps/file-manager-sdk/

# handler SDK 설치
RUN pip install --no-cache-dir \
    /deps/llm-handler-sdk \
    /deps/mdb-handler-sdk \
    /deps/redis-handler \
    /deps/file-manager-sdk

# service 의존성 설치
COPY packages/llm-request/service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# service 코드 복사
COPY packages/llm-request/service/app/ ./app/

EXPOSE 8101
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8101"]
