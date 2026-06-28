#!/bin/sh

# Generate self-signed certificates for SSL/TLS testing

CERT_DIR="/certs"
mkdir -p "$CERT_DIR"

# Generate private key and certificate for server
echo "Generating server certificate..."
openssl req -x509 -newkey rsa:4096 -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.crt" -days 365 -nodes \
  -subj "/C=US/ST=State/L=City/O=NetEngine/CN=localhost"

# Generate CA certificate for client validation
echo "Generating client CA certificate..."
openssl req -x509 -newkey rsa:4096 -keyout "$CERT_DIR/client-ca.key" -out "$CERT_DIR/client-ca.crt" -days 365 -nodes \
  -subj "/C=US/ST=State/L=City/O=NetEngine/CN=netengine-ca"

# Generate client certificate signed by CA
echo "Generating client certificate..."
openssl req -new -newkey rsa:4096 -keyout "$CERT_DIR/client.key" -out "$CERT_DIR/client.csr" \
  -subj "/C=US/ST=State/L=City/O=NetEngine/CN=netengine-client"

openssl x509 -req -in "$CERT_DIR/client.csr" -CA "$CERT_DIR/client-ca.crt" -CAkey "$CERT_DIR/client-ca.key" \
  -CAcreateserial -out "$CERT_DIR/client.crt" -days 365

# Set permissions
chmod 600 "$CERT_DIR"/*.key
chmod 644 "$CERT_DIR"/*.crt

echo "Certificates generated in $CERT_DIR:"
ls -la "$CERT_DIR"

# Display certificate details
echo ""
echo "Server Certificate:"
openssl x509 -in "$CERT_DIR/server.crt" -text -noout | grep -A 2 "Subject:\|Issuer:\|Not Before\|Not After"

echo ""
echo "Client Certificate:"
openssl x509 -in "$CERT_DIR/client.crt" -text -noout | grep -A 2 "Subject:\|Issuer:\|Not Before\|Not After"
