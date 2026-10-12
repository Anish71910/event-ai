"""Requires Node + jsdom; runs only on an isolated temporary demo database."""
import os,subprocess,sys,tempfile,time,urllib.request,socket
from pathlib import Path
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='eventai-ui-') as folder:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,APP_ENV='development',DEMO_MODE='1',EVENTAI_WORKER='0',EMAIL_WORKER='0',SMTP_PASSWORD='',PAYMENT_PROVIDER='demo',DATABASE=str(Path(folder)/'test.db'),PORT=str(port),EVENTAI_TEST_URL=f'http://127.0.0.1:{port}')
    with open(Path(folder)/'server.log','w+') as log:
        server=subprocess.Popen([sys.executable,'app.py'],cwd=root,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen(env['EVENTAI_TEST_URL']+'/healthz',timeout=1);break
                except OSError:time.sleep(.1)
            result=subprocess.run(['node',str(root/'tests/ui_smoke.cjs')],env=env,timeout=100)
            if result.returncode:log.seek(0);print(log.read()[-8000:])
            raise SystemExit(result.returncode)
        finally:server.terminate();server.wait(timeout=5)
