#!/usr/bin/env python3
"""Prove model startup and generation with container networking disabled."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'qwen38-offline-check'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    if not re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}', args.image):
        parser.error('--image must be a registry digest or a local immutable image ID')
    env = dict(os.environ, VLLM_IMAGE=args.image)
    bundle = Path(args.bundle)/'bundle.json'
    manifest = json.loads(bundle.read_text())
    actual_image = subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',args.image],text=True).strip()
    if actual_image != manifest['image_id']:
        parser.error('Selected image does not match the offline bundle')
    command = ['docker','compose','--project-directory',str(ROOT),'-p',PROJECT,
               '-f',str(ROOT/'compose.yaml'),'-f',str(ROOT/'offline/compose.verify.yaml')]
    try:
        subprocess.run(command+['up','-d','--pull','never'],env=env,check=True)
        identifier = subprocess.check_output(command+['ps','--all','-q','qwen'],env=env,text=True).strip()
        deadline = time.monotonic()+args.timeout
        while time.monotonic()<deadline:
            state = json.loads(subprocess.check_output(
                ['docker','inspect','--format','{{json .State}}',identifier],text=True))
            if state.get('Health',{}).get('Status') == 'healthy':
                break
            if state.get('Status') in ['exited','dead']:
                raise RuntimeError('Offline model process exited')
            time.sleep(5)
        else:
            raise TimeoutError('Offline startup timed out')
        # Reading the secret inside the container avoids putting the key into argv/logs.
        probe = '''import json,os,urllib.request
from pathlib import Path
key=Path('/run/secrets/qwen_api_key').read_text().strip()
data={'model':os.environ['SERVED_MODEL_NAME'],'messages':[{'role':'user','content':'Say hello.'}],
      'max_tokens':32,'chat_template_kwargs':{'enable_thinking':False}}
req=urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions',data=json.dumps(data).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
with urllib.request.urlopen(req,timeout=300) as response:
    answer=json.load(response)['choices'][0]['message'].get('content')
    assert answer and answer.strip(), 'No answer returned'
print('PASS: generation with network_mode=none')
'''
        subprocess.run(command+['exec','-T','qwen','python3','-c',probe],env=env,check=True)
        config = json.loads(subprocess.check_output(command+['config','--format','json'],env=env,text=True))
        settings = config['services']['qwen']['environment']
        manifest['gpu_offline_acceptance'] = {
            'status':'passed','image_id':actual_image,
            'model_id':settings['MODEL_ID'],'model_revision':settings['MODEL_REVISION'],
            'time_utc':datetime.now(timezone.utc).isoformat(), 'network_mode':'none',
        }
        bundle.write_text(json.dumps(manifest,indent=2)+'\n')
        print('GPU offline acceptance passed; export this project\'s caches before transfer.')
    except Exception:
        manifest['gpu_offline_acceptance'] = {'status':'failed','image_id':actual_image}
        bundle.write_text(json.dumps(manifest,indent=2)+'\n')
        subprocess.run(command+['logs','--tail','80','qwen'],env=env,check=False)
        raise
    finally:
        # Keep cache volumes for export. Never operates on the production project.
        subprocess.run(command+['down'],env=env,check=False)


if __name__ == '__main__':
    main()
