#!/usr/bin/env python3
"""Verify archives before loading an offline image and restoring its caches."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from validate_model import sha256
from export_caches import CACHES


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bundle',required=True)
    parser.add_argument('--project',default='qwen38-offline-check')
    parser.add_argument('--replace-caches',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch('[a-z0-9][a-z0-9_-]*',args.project):
        parser.error('Invalid Compose project name')
    directory=Path(args.bundle).resolve()
    manifest=json.loads((directory/'bundle.json').read_text())
    for file,field in [('inference-image.tar','image_sha256'),('runtime-cache.tar.gz','cache_sha256')]:
        if sha256(directory/file)!=manifest[field]:
            raise RuntimeError(f'Bundle checksum mismatch: {file}')
    proof=manifest.get('gpu_offline_acceptance',{})
    if not isinstance(proof,dict) or proof.get('status')!='passed':
        raise RuntimeError('Source bundle has no successful offline acceptance record')
    volumes=[args.project+'_'+name for name in CACHES]
    present=[v for v in volumes if subprocess.run(['docker','volume','inspect',v],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0]
    if present and not args.replace_caches:
        parser.error('Target cache volumes exist; stop the target service and explicitly use --replace-caches')
    subprocess.run(['docker','load','-i',str(directory/'inference-image.tar')],check=True)
    image=manifest['image_id']
    actual=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',image],text=True).strip()
    if actual!=image:
        raise RuntimeError('Loaded image identity mismatch')
    if present:
        # Docker refuses to remove a volume still used by a container.
        subprocess.run(['docker','volume','rm',*present],check=True)
    mounts=[]
    for name,volume in zip(CACHES,volumes):
        subprocess.run(['docker','volume','create',volume],check=True,stdout=subprocess.DEVNULL)
        mounts += ['--mount',f'type=volume,source={volume},target=/cache/{name}']
    subprocess.run(['docker','run','--rm','--network','none','--entrypoint','tar',*mounts,
        '--mount',f'type=bind,source={directory},target=/bundle,readonly',image,
        '-C','/cache','-xzf','/bundle/runtime-cache.tar.gz'],check=True)
    print('Restored image/caches. Set VLLM_IMAGE='+image)
    print('Repeat disconnected GPU acceptance on this target before production use.')


if __name__=='__main__':
    main()
