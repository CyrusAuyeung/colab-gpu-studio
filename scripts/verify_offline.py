"""Reproducible local-only verification. Owns and stops both fresh demo servers."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/offline-evidence'


def clean_environment():
    env={k:v for k,v in os.environ.items() if not k.startswith(('GOOGLE_','GPU_','STUDIO_','DATABASE_','DATA_','ENCRYPTION_','SESSION_','ATELIER_'))}
    env.update(GPU_REAL_ENABLED='0',GPU_STORAGE='local',ENVIRONMENT='offline-test')
    return env


def run(command,env,log):
    with log.open('w') as handle:
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=handle,stderr=subprocess.STDOUT)
    if result.returncode:
        print(log.read_text()[-12000:])
        raise RuntimeError(f'check failed: {command}; log={log}')
    print(f'PASS {command}; log={log}',flush=True)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def browser_group(fault,checks,env):
    port=free_port()
    base=f'http://127.0.0.1:{port}'
    env={**env,'OFFLINE_BROWSER_BASE':base,'STUDIO_OFFLINE_AUDIT_PATH':str(OUT/('fault-network-audit.json' if fault else 'normal-network-audit.json'))}
    command=[sys.executable,'scripts/offline_server.py','--port',str(port)]
    if fault:
        command.append('--fail-first-start')
    with tempfile.TemporaryFile(mode='w+') as output:
        server=subprocess.Popen(command,cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+25
            with httpx.Client(trust_env=False,timeout=1) as client:
                while True:
                    if server.poll() is not None:
                        output.seek(0)
                        raise RuntimeError('demo server exited: '+output.read()[-4000:])
                    try:
                        if client.get(base+'/api/health').status_code==200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic()>deadline:
                        raise RuntimeError('demo readiness timed out')
                    time.sleep(0.1)
            for script in checks:
                run([sys.executable,'scripts/'+script+'.py'],env,OUT/(script+'.log'))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    env=clean_environment()
    # A failed run must never leave an older success marker or audit behind.
    for name in ('verification-run.json','network-audit.json','normal-network-audit.json','fault-network-audit.json'):
        (OUT/name).unlink(missing_ok=True)
    run([sys.executable,'-m','ruff','check','api','tests'],env,OUT/'ruff.log')
    run([sys.executable,'-m','pytest','-q'],env,OUT/'pytest.log')
    run(['npm','--prefix','web','run','test:contract'],env,OUT/'frontend-contract.log')
    run(['npm','--prefix','web','run','build'],env,OUT/'frontend-build.log')
    browser_group(False,['offline_browser_check'],env)
    browser_group(True,['offline_attempt_browser_check','offline_list_browser_check'],env)
    audit_files=['network-audit.json','normal-network-audit.json','fault-network-audit.json']
    audits={}
    for name in audit_files:
        value=json.loads((OUT/name).read_text())
        assert not value.get('blocked_attempts'),value
        assert all(value.get(key,0)==0 for key in ('real_runtime_create_count','real_runtime_delete_count','real_mcp_call_count','real_colab_oauth_call_count')),value
        audits[name]=value
    (OUT/'verification-run.json').write_text(json.dumps({'passed':True,'fresh_servers':2,'audits':audits},indent=2))
    print('PASS: fresh-server offline verification; no real API attempts',flush=True)


if __name__=='__main__':
    main()
