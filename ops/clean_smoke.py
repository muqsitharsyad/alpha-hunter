"""Boot an isolated image with fresh SQLite storage; never touch production data."""
import json
import os
import secrets
import subprocess
import time
from datetime import datetime, timezone


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def main():
    image = os.environ["SMOKE_IMAGE"]
    name = "alphahunter-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    volume = name + "-data"
    env = os.environ.copy()
    env.update(SECRET_KEY=secrets.token_hex(32), BOOTSTRAP_PASSWORD=secrets.token_urlsafe(32))
    run("docker", "volume", "create", volume)
    try:
        run("docker", "run", "-d", "--name", name, "--network", "none",
            "--memory", "384m", "-v", volume + ":/data",
            "-e", "SECRET_KEY", "-e", "BOOTSTRAP_PASSWORD",
            "-e", "BOOTSTRAP_EMAIL=smoke@alphahunter.local",
            "-e", "APP_ENV=production", "-e", "DEMO_MODE=false",
            "-e", "PRIVATE_UI_NO_AUTH=true", "-e", "PUBLIC_ORIGIN=https://smoke.ts.net",
            "-e", "TRADING_MODE=paper", "-e", "ENABLE_LIVE_EXECUTION=false", image, env=env)
        for _ in range(45):
            status = run("docker", "inspect", "--format", "{{.State.Health.Status}}", name)
            if status == "healthy":
                break
            time.sleep(2)
        assert status == "healthy", status
        probe = '''import json,sqlite3,urllib.request,urllib.error
from app.config import get_settings
from app.execution import EmergencyLayer
cfg=get_settings(); assert cfg.trading_mode=='paper' and not cfg.enable_live_execution
pages=['dashboard','positions','opportunities','smart-money','trades','learning','health','settings']
for p in pages:
 r=urllib.request.urlopen('http://127.0.0.1:8010/app/'+p); body=r.read().decode(); assert r.status==200 and 'PAPER SYSTEM' in body,p
try:
 urllib.request.urlopen('http://127.0.0.1:8010/dashboard/summary'); raise AssertionError('API exposed')
except urllib.error.HTTPError as e: assert e.code==401
c=sqlite3.connect('/data/alphahunter.db'); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
assert c.execute('select count(*) from paper_accounts').fetchone()[0]==1
assert c.execute('select count(*) from paper_trades').fetchone()[0]==0
assert EmergencyLayer().get_state()['status'] != 'running'
print(json.dumps({'pages':len(pages),'api':401,'sqlite':'ok','accounts':1,'trades':0,'paper_only':True}))'''
        print(run("docker", "exec", name, "python", "-c", probe))
        print(json.dumps({"status": "PASS", "container": name, "volume": volume, "image": image}))
    finally:
        # Retain isolated storage for inspection; stop only this drill container.
        subprocess.run(["docker", "stop", name], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
