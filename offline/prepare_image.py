#!/usr/bin/env python3
"""Build/save a pinned kernel image on an online Linux Docker host."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validate_model import sha256


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-image', default=json.loads((ROOT/'image-lock.json').read_text())['image'])
    parser.add_argument('--output', required=True)
    parser.add_argument('--tag', default='qwen38-vllm-offline:v0.32.0')
    args = parser.parse_args()
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', args.base_image):
        parser.error('--base-image must be pinned by registry digest')
    output = Path(args.output).resolve()
    if output.exists():
        parser.error('--output must be a new directory')
    output.mkdir(parents=True, mode=0o700)
    subprocess.run(['docker','build','--build-arg','BASE_IMAGE='+args.base_image,
                    '-f',str(ROOT/'offline/Dockerfile'),'-t',args.tag,str(ROOT/'offline')],check=True)
    metadata = json.loads(subprocess.check_output(['docker','image','inspect',args.tag],text=True))[0]
    if metadata['Os'] != 'linux':
        raise RuntimeError('Expected Linux image')
    archive = output/'inference-image.tar'
    subprocess.run(['docker','save','-o',str(archive),args.tag],check=True)
    manifest = {'schema_version':1,'base_image':args.base_image,'image_id':metadata['Id'],
                'platform':metadata['Os']+'/'+metadata['Architecture'],
                'image_sha256':sha256(archive),'gpu_offline_acceptance':'not-yet-run'}
    (output/'bundle.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Saved pinned kernel image. Required next step: disconnected GPU/model acceptance.')
    print('Runtime VLLM_IMAGE='+metadata['Id'])


if __name__ == '__main__':
    main()
