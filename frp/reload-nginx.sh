#!/bin/sh
set -eu
# Certbot deploy hook: installed on the cloud host, executed after renewal.
/usr/sbin/nginx -t
/usr/bin/systemctl reload nginx
