FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# SSL 인증서 (사내 프록시 환경)
COPY certs/ePrism.crt /usr/local/share/ca-certificates/ePrism.crt
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

EXPOSE 8600

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8600"]
