import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PACKAGE = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
OPENSSL = os.environ.get('OPENSSL_BIN', 'openssl')
ENV = dict(os.environ)
if '/' in OPENSSL:
    ENV['PATH'] = str(Path(OPENSSL).parent) + os.pathsep + ENV['PATH']


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_port(port, process):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError('FRP process exited before port became available')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('FRP port did not become available')


class MockBackend(BaseHTTPRequestHandler):
    finish_stream = threading.Event()
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer qa-api-key-independent-of-frp-token':
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        if self.path == '/sse':
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'data: first\n\n')
            self.wfile.flush()
            self.finish_stream.wait(timeout=10)
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        else:
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"mock-model-no-real-inference"}]}')


def main():
    global TOOLS
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin-dir', required=True)
    args = parser.parse_args()
    TOOLS = Path(args.bin_dir).resolve()
    for role in ['frps', 'frpc']:
        version = subprocess.check_output([str(TOOLS/role), '--version'], text=True).strip()
        assert version == '0.71.1', version
    processes = []
    backend = None
    with tempfile.TemporaryDirectory(dir=TOOLS, prefix='run-') as scratch:
        scratch = Path(scratch)
        try:
            pki = scratch / 'pki'
            subprocess.run(['bash', str(PACKAGE/'frp/make-frp-pki.sh'), 'frp.example.com', str(pki)],
                           env=ENV, check=True, capture_output=True)
            subprocess.run([OPENSSL, 'verify',
                            '-CAfile', str(pki/'ca.crt'), '-verify_hostname', 'frp.example.com',
                            str(pki/'server.crt')], check=True, capture_output=True)
            print('PASS private CA generation, leaf chain and SAN/hostname')
            backend = ThreadingHTTPServer(('127.0.0.1', 0), MockBackend)
            worker = threading.Thread(target=backend.serve_forever, daemon=True)
            worker.start()
            bind_port, remote_port = free_port(), free_port()
            while remote_port == bind_port:
                remote_port = free_port()
            server = (PACKAGE/'frp/frps.toml').read_text()
            client = (PACKAGE/'frp/frpc.toml').read_text()
            for name in ['server.crt', 'server.key', 'ca.crt']:
                server=server.replace('/etc/frp/pki/'+name, str(pki/name))
                client=client.replace('/etc/frp/pki/'+name, str(pki/name))
            server=server.replace('/etc/frp/frp_token',str(pki/'frp_token'))
            client=client.replace('/etc/frp/frp_token',str(pki/'frp_token'))
            server=server.replace('bindAddr = "0.0.0.0"','bindAddr = "127.0.0.1"')
            server=server.replace('bindPort = 7000',f'bindPort = {bind_port}')
            server=server.replace('single = 18000',f'single = {remote_port}')
            client=client.replace('serverAddr = "frp.example.com"','serverAddr = "127.0.0.1"')
            client=client.replace('serverPort = 7000',f'serverPort = {bind_port}')
            client=client.replace('localPort = 8000',f'localPort = {backend.server_port}')
            client=client.replace('remotePort = 18000',f'remotePort = {remote_port}')
            for role, text in [('frps',server),('frpc',client)]:
                config=scratch/(role+'.toml');config.write_text(text)
                result=subprocess.run([str(TOOLS/role),'verify','-c',str(config)],capture_output=True,text=True)
                assert result.returncode==0, result.stdout+result.stderr
                print('PASS',role,'v0.71.1 strict config validation')
            def start(role, config_name):
                log=scratch/(config_name+'.log')
                with log.open('wb') as output:
                    process=subprocess.Popen([str(TOOLS/role),'-c',str(scratch/config_name)],stdout=output,stderr=subprocess.STDOUT)
                processes.append(process)
                return process,log
            frps,_=start('frps','frps.toml')
            wait_port(bind_port,frps)
            frpc,_=start('frpc','frpc.toml')
            wait_port(remote_port,frpc)
            url=f'http://127.0.0.1:{remote_port}'
            try:
                urllib.request.urlopen(url+'/v1/models',timeout=5)
                raise AssertionError('Unauthenticated request unexpectedly succeeded')
            except urllib.error.HTTPError as error:
                assert error.code==401
            headers={'Authorization':'Bearer qa-api-key-independent-of-frp-token'}
            with urllib.request.urlopen(urllib.request.Request(url+'/v1/models',headers=headers),timeout=5) as response:
                assert json.load(response)['data'][0]['id']=='mock-model-no-real-inference'
            with urllib.request.urlopen(urllib.request.Request(url+'/sse',headers=headers),timeout=5) as response:
                assert response.readline()==b'data: first\n'
                assert not MockBackend.finish_stream.is_set(), 'SSE first chunk was buffered'
                MockBackend.finish_stream.set()
                assert b'[DONE]' in response.read()
            print('PASS real FRP TLS tunnel: HTTP, Authorization header, SSE first chunk before completion')
            for label,modified,expected in [
                ('wrong-tls-name',client.replace('transport.tls.serverName = "frp.example.com"',
                    'transport.tls.serverName = "wrong.example.com"'),['certificate','wrong.example.com']),
                ('wrong-token',client.replace(str(pki/'frp_token'),str(scratch/'wrong_token')),['token'])
            ]:
                (scratch/'wrong_token').write_text('wrong-token-not-a-production-secret')
                name=label+'.toml'
                (scratch/name).write_text(modified.replace('loginFailExit = false','loginFailExit = true'))
                process,log=start('frpc',name)
                process.wait(timeout=15)
                message=log.read_text().lower()
                # tcpMux may collapse a TLS handshake error to 'session shutdown'.
                # Require an actual failed login, not merely a later proxy-name conflict.
                assert process.returncode != 0, message
                assert ('login to the server failed' in message or
                        'login to server failed' in message), message
                if label == 'wrong-token':
                    assert 'token' in message, message
                assert 'login to server success' not in message
                print('PASS rejected',label)
            # A client with a valid token must still respect the server's port allowlist.
            forbidden=free_port()
            while forbidden in [bind_port,remote_port,backend.server_port]: forbidden=free_port()
            config=client.replace('name = "qwen38-api"','name = "qa-port-not-allowed"')
            config=config.replace(f'remotePort = {remote_port}',f'remotePort = {forbidden}')
            (scratch/'wrong-port.toml').write_text(config)
            process,log=start('frpc','wrong-port.toml')
            end=time.monotonic()+10
            while time.monotonic()<end and 'not allowed' not in log.read_text().lower(): time.sleep(0.1)
            assert 'not allowed' in log.read_text().lower(),log.read_text()
            print('PASS server rejects remote ports outside allowPorts')
            print('All local FRP checks passed; mock backend only, no GPU or real cloud involved.')
        finally:
            MockBackend.finish_stream.set()
            if backend is not None:
                backend.shutdown()
                backend.server_close()
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()
            # TemporaryDirectory removes test tokens and CA/private keys.


if __name__=='__main__':
    main()
