#!/usr/bin/env python3
"""Fail closed when the mounted checkpoint differs from the configured revision."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate(root, repo, revision, full_hash=False):
    root = Path(root).resolve()
    if not repo or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('MODEL_ID and a full MODEL_REVISION are required')
    manifest = json.loads((root / 'deployment-manifest.json').read_text())
    if manifest.get('schema_version') != 2:
        raise ValueError('Manifest v2 required; rerun download-model.py for the same repo/revision')
    if (manifest.get('repo'), manifest.get('revision')) != (repo, revision):
        raise ValueError('Mounted model repo/revision does not match configuration')
    if sha256(root / 'config.json') != manifest.get('config_sha256'):
        raise ValueError('Model config checksum mismatch')
    index = json.loads((root / 'model.safetensors.index.json').read_text())
    if sha256(root / 'model.safetensors.index.json') != manifest.get('index_sha256'):
        raise ValueError('Model index checksum mismatch')
    records = manifest.get('files', {})
    shards = set(index['weight_map'].values())
    if not shards or not shards.issubset(records):
        raise ValueError('Manifest does not cover all weight shards')
    # Configs, processor/tokenizer/template files are hashed on every startup.
    # Large weights use size checks unless the full offline acceptance scan is requested.
    for name, record in records.items():
        path = root / name
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError(f'Missing or unsafe model file: {name}')
        if path.stat().st_size != record['bytes']:
            raise ValueError(f'Model file size mismatch: {name}')
        if (full_hash or name not in shards) and sha256(path) != record['sha256']:
            raise ValueError(f'Model file checksum mismatch: {name}')
    print(f'PASS: mounted {repo}@{revision}; {len(shards)} shards; full_hash={full_hash}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--full-hash', action='store_true')
    args = parser.parse_args()
    try:
        validate(args.model_dir, args.repo, args.revision, args.full_hash)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'Model validation failed: {error}\n')
