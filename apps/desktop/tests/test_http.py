"""Real local server + real B process; no model account required."""
import json
import base64
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[3]

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]

class HttpTests(unittest.TestCase):
    def test_server_package_image_and_owned_process_shutdown(self):
        port,studio=free_port(),free_port()
        proc=subprocess.Popen([sys.executable,str(ROOT/'apps/desktop/server.py'),'--offline','--no-browser','--port',str(port)],
            cwd=ROOT,env={**os.environ,'STUDIO_PORT':str(studio)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        base=f'http://127.0.0.1:{port}'
        def request(path,data=None):
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,
                headers={'Content-Type':'application/json'})
            return urllib.request.urlopen(req,timeout=5)
        try:
            deadline=time.monotonic()+15
            while True:
                try:
                    with request('/api/state') as response:state=json.load(response)
                    break
                except (OSError,urllib.error.URLError):
                    if proc.poll() is not None or time.monotonic()>deadline:self.fail('服务未能启动')
                    time.sleep(.05)
            self.assertEqual(len(state['scene']['roles']),3)
            with request('/') as response:self.assertIn(b'/web/main.js',response.read())
            for path in ('/.env','/web/../../.env'):
                with self.assertRaises(urllib.error.HTTPError) as error:request(path)
                self.assertIn(error.exception.code,(403,404))
            # Static resource is the currently decoded A frame.
            with request('/resource/'+state['scene']['roles'][0]['avatarRef']) as response:
                self.assertTrue(response.read().startswith(b'\x89PNG'))
            raw=(ROOT/'modules/interaction/fixtures/share.png').read_bytes()
            with request('/api/prepare',dict(dataBase64=base64.b64encode(raw).decode(),mime='image/png',name='share.png',sessionId=state['view']['sessionId'])) as response:
                item=json.load(response)
            with request('/api/dispatch',dict(protocol='wimipet/1',sessionId=state['view']['sessionId'],requestId='http-image',expiresAtMs=int(time.time()*1000)+30000,type='UserInput',payload=dict(kind='message',recipient='all',text='',attachments=[item]))) as response:
                self.assertTrue(json.load(response)['payload']['accepted'])
            # A draft-release race must not invalidate an accepted attachment.
            request('/api/release',dict(contentRef=item['contentRef'])).close()
            with request('/resource/'+item['contentRef']) as response:self.assertEqual(response.read(),raw)
            deadline=time.monotonic()+5
            while True:
                with request('/api/state') as response:current=json.load(response)
                if len([m for m in current['view']['messages'] if m['speaker']['type']=='character'])==3:break
                if time.monotonic()>deadline:self.fail('图片未获得三个角色的回复')
                time.sleep(.02)
            with request('/api/exit',{}) as response:self.assertTrue(json.load(response)['closed'])
            proc.wait(timeout=15)
            self.assertEqual(proc.returncode,0)
            with socket.socket() as sock:
                self.assertNotEqual(sock.connect_ex(('127.0.0.1',studio)),0)
        finally:
            if proc.poll() is None:
                try:request('/api/exit',{}).close();proc.wait(timeout=15)
                except Exception:proc.terminate();proc.wait(timeout=5)
            proc.stderr.close()

if __name__=='__main__':unittest.main()
