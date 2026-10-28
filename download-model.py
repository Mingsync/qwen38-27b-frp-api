#!/usr/bin/env python3
"""Download a pinned checkpoint; verify shard sizes against Hugging Face metadata."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def main():
    from huggingface_hub import HfApi, snapshot_download
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default='Qwen/Qwen3.8-27B-FP8')
    parser.add_argument('--revision', default='017b9c7af6b5689d5dd426a76e0bc077eb5ca20a')
    parser.add_argument('--output', default='/srv/models/Qwen3.8-27B-FP8')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
        parser.error('--revision must be a full 40-character commit hash')
    info = HfApi().model_info(args.repo, revision=args.revision, files_metadata=True)
    if info.sha != args.revision:
        raise SystemExit('Resolved revision mismatch')
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Download the complete repository: processors, tokenizer and templates matter.
    snapshot_download(repo_id=args.repo, revision=args.revision,
                      local_dir=str(root), max_workers=4)
    index = json.loads((root / 'model.safetensors.index.json').read_text())
    weights = sorted(set(index['weight_map'].values()))
    remote = {f.rfilename: f for f in info.siblings}
    weight_bytes = 0
    for name in weights:
        file = root / name
        if not file.is_file() or file.stat().st_size == 0:
            raise SystemExit(f'Missing or empty shard: {name}')
        expected = getattr(remote.get(name), 'size', None)
        if expected is not None and file.stat().st_size != expected:
            raise SystemExit(f'Size mismatch: {name}')
        weight_bytes += file.stat().st_size
    config = json.loads((root / 'config.json').read_text())
    manifest = {
        'repo': args.repo, 'revision': info.sha, 'weight_files': len(weights),
        'weight_file_bytes': weight_bytes,
        'architecture': config.get('architectures'),
        'quantization': config.get('quantization_config', {}).get('quant_method'),
        'config_sha256': hashlib.sha256((root / 'config.json').read_bytes()).hexdigest(),
    }
    (root / 'deployment-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
