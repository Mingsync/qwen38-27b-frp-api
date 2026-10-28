#!/usr/bin/env bash
set -euo pipefail
# Run on a trusted admin machine; keep ca.key offline and out of the deployment package.
tls_name="${1:-}"
dest="${2:-}"
[[ -n "$tls_name" && -n "$dest" ]] || { echo 'Usage: bash make-frp-pki.sh frp.example.com NEW_OUTPUT_DIRECTORY' >&2; exit 2; }
[[ "$tls_name" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || { echo 'Invalid DNS name' >&2; exit 2; }
[[ ! -e "$dest" ]] || { echo 'Output directory exists; refusing to overwrite PKI.' >&2; exit 2; }
umask 077
mkdir -p -- "$dest"
openssl genrsa -out "$dest/ca.key" 3072
openssl req -x509 -new -sha256 -days 3650 -key "$dest/ca.key" \
  -subj '/CN=Qwen FRP private CA' -out "$dest/ca.crt" \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign'
openssl genrsa -out "$dest/server.key" 3072
openssl req -new -sha256 -key "$dest/server.key" \
  -subj "/CN=$tls_name" -out "$dest/server.csr"
cat > "$dest/server.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:$tls_name
EOF
openssl x509 -req -sha256 -days 397 -in "$dest/server.csr" \
  -CA "$dest/ca.crt" -CAkey "$dest/ca.key" -CAcreateserial \
  -extfile "$dest/server.ext" -out "$dest/server.crt"
openssl verify -CAfile "$dest/ca.crt" "$dest/server.crt"
openssl rand -hex 32 > "$dest/frp_token"
chmod 600 "$dest/ca.key" "$dest/server.key" "$dest/frp_token"
printf '%s\n' 'Generated FRP PKI and a tunnel token. No secrets printed.' \
  'Cloud: server.crt, server.key, frp_token. GPU host: ca.crt, frp_token.' \
  'Keep ca.key offline; never copy it into the deployment package.'
