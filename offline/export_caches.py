#!/usr/bin/env python3
"""Export warmed caches from the isolated offline acceptance project."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from validate_model import sha256

CACHES = ['vllm_cache','triton_cache','flashinfer_cache','torch_cache']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bundle',required=True)
    args=parser.parse_args()
    directory=Path(args.bundle).resolve()
    path=directory/'bundle.json'
    manifest=json.loads(path.read_text())
    proof=manifest.get('gpu_offline_acceptance',{})
    if not isinstance(proof,dict) or proof.get('status')!='passed' or proof.get('image_id')!=manifest['image_id']:
        raise RuntimeError('Run successful offline GPU acceptance for this image before exporting caches')
    if sha256(directory/'inference-image.tar') != manifest['image_sha256']:
        raise RuntimeError('Image archive checksum mismatch')
    mounts=[]
    for name in CACHES:
        volume='qwen38-offline-check_'+name
        subprocess.run(['docker','volume','inspect',volume],check=True,stdout=subprocess.DEVNULL)
        mounts += ['--mount',f'type=volume,source={volume},target=/cache/{name},readonly']
    subprocess.run(['docker','run','--rm','--network','none','--entrypoint','tar',*mounts,
                    '--mount',f'type=bind,source={directory},target=/bundle',
                    manifest['image_id'],'-C','/cache','-czf','/bundle/runtime-cache.tar.gz','.'],check=True)
    manifest['cache_sha256']=sha256(directory/'runtime-cache.tar.gz')
    manifest['cache_source_project']='qwen38-offline-check'
    path.write_text(json.dumps(manifest,indent=2)+'\n')
    print('Saved cache archive and checksum. Preserve the separate offline acceptance log.')


if __name__=='__main__':
    main()
