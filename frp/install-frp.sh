#!/usr/bin/env bash
set -euo pipefail
role="${1:-}"
[[ "$role" == frps || "$role" == frpc ]] || { echo 'Usage: bash install-frp.sh frps|frpc' >&2; exit 2; }
[[ "$(uname -s)" == Linux ]] || { echo 'This installer targets Linux only.' >&2; exit 2; }
version=0.71.1
case "$(uname -m)" in
  x86_64) arch=amd64; digest=1d9875210972a5d1a62b2dc4c48df45ab0af05d3ca1b5e494a014428259df803 ;;
  aarch64|arm64) arch=arm64; digest=8edf590b0c396fa42f9c147121a76af83cfc2002b08f5aef0ea1008c43482019 ;;
  *) echo 'Supported architectures: Linux x86_64 or aarch64.' >&2; exit 2 ;;
esac
task_tmp="$(mktemp -d)"
trap 'rm -rf -- "$task_tmp"' EXIT
archive="frp_${version}_linux_${arch}.tar.gz"
curl -fL --retry 3 "https://github.com/fatedier/frp/releases/download/v${version}/${archive}" -o "$task_tmp/$archive"
printf '%s  %s\n' "$digest" "$task_tmp/$archive" | sha256sum -c -
tar -xzf "$task_tmp/$archive" -C "$task_tmp"
privilege=()
if [[ "$EUID" != 0 ]]; then privilege=(sudo); fi
"${privilege[@]}" install -d -m 755 "/usr/local/lib/frp/$version"
"${privilege[@]}" install -m 755 "$task_tmp/frp_${version}_linux_${arch}/$role" "/usr/local/lib/frp/$version/$role"
"${privilege[@]}" ln -sfn "/usr/local/lib/frp/$version/$role" "/usr/local/bin/$role"
"/usr/local/bin/$role" --version
printf 'Installed %s %s; no service was started.\n' "$role" "$version"
