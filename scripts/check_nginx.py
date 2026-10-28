#!/usr/bin/env python3
"""Validate every shipped Nginx template using temporary, non-production TLS files."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    nginx = shutil.which('nginx') or '/usr/sbin/nginx'
    with tempfile.TemporaryDirectory(prefix='qwen38-nginx-') as directory:
        temp = Path(directory)
        (temp/'logs').mkdir()
        subprocess.run([os.getenv('OPENSSL_BIN','openssl'),'req','-x509','-newkey','rsa:2048',
            '-nodes','-days','1','-subj','/CN=llm.example.com',
            '-keyout',str(temp/'privkey.pem'),'-out',str(temp/'fullchain.pem')],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        snippet = temp/'proxy.conf'
        snippet.write_text((ROOT/'nginx/qwen38-proxy.conf').read_text())
        templates = ['nginx/qwen38-http.conf','nginx/qwen38-https.conf',
                     'frp/cloud-nginx-bootstrap.conf','frp/cloud-nginx-https.conf']
        for name in templates:
            text = (ROOT/name).read_text()
            text = text.replace('/etc/nginx/snippets/qwen38-proxy.conf',str(snippet))
            text = text.replace('/etc/letsencrypt/live/llm.example.com/',str(temp)+'/')
            include = temp/'site.conf'
            include.write_text(text)
            main = temp/'nginx.conf'
            temporary_paths = '\n'.join(f'{module}_temp_path {temp}/{module};'
                for module in ['client_body','proxy','fastcgi','uwsgi','scgi'])
            main.write_text(f'pid {temp}/nginx.pid;\nerror_log stderr;\nevents {{}}\n'
                            f'http {{ access_log off; {temporary_paths}\ninclude {include}; }}\n')
            subprocess.run([nginx,'-t','-p',str(temp)+'/', '-c',str(main)],check=True)
            print('PASS nginx -t:',name)


if __name__ == '__main__':
    main()
