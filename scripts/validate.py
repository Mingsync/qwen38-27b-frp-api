#!/usr/bin/env python3
"""Validate versioned configuration without downloading weights or running GPUs.

Requires Python 3.11+ for the standard-library TOML parser.
"""
import ast
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_CONTENT = [
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{30,}'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'/Users/[A-Za-z0-9_-]+/(?:Desktop|Documents|\.codex)/'),
]


def main():
    files = []
    for entry in (ROOT / 'SHA256SUMS').read_text().splitlines():
        digest, relative = entry.split('  ', 1)
        path = ROOT / relative
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError('Manifest path escapes repository')
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing regular file: {relative}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Checksum mismatch: {relative}')
        if (path.name in {'.env', 'frp_token'} or 'secrets' in Path(relative).parts
                or path.suffix in {'.key', '.pem', '.p12', '.pfx'}):
            raise ValueError(f'Sensitive file in publication manifest: {relative}')
        text = path.read_text()
        if any(pattern.search(text) for pattern in SENSITIVE_CONTENT):
            # Print filenames only; never echo potentially sensitive matched text.
            raise ValueError(f'Credential or personal-path pattern: {relative}')
        if path.suffix == '.py':
            ast.parse(text, filename=relative)
        elif path.suffix == '.sh':
            subprocess.run(['bash', '-n', str(path)], check=True)
        elif path.suffix == '.toml':
            tomllib.loads(text)
        elif path.suffix == '.md' and text.count('```') % 2:
            raise ValueError(f'Unclosed Markdown code fence: {relative}')
        files.append(relative)

    if len(files) != len(set(files)):
        raise ValueError('Duplicate file in SHA256SUMS')
    # All tracked files must be accounted for, including future additions.
    tracked = subprocess.check_output(
        ['git', '-C', str(ROOT), 'ls-files', '-z'], text=True).split('\0')
    missing = sorted(set(filter(None, tracked)) - set(files) - {'SHA256SUMS'})
    if missing:
        raise ValueError('Tracked files missing from manifest: ' + ', '.join(missing))
    print(f'PASS: {len(files)} manifest files; syntax, checksums and publication scan')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'FAIL: {error}', file=sys.stderr)
        sys.exit(1)
