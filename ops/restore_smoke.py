"""Restore an online production backup to an isolated, network-disabled container."""
import json
import os
import secrets
import subprocess
import time
from datetime import datetime, timezone


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


name = 'alphahunter-restore-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
image = os.environ['SMOKE_IMAGE']
backup = '/data/' + name + '.db'
volume = name + '-data'
# SQLite backup API captures WAL consistently; never expose the contents.
run('docker', 'exec', 'alphahunter', 'python', '-c',
    "import sqlite3; s=sqlite3.connect('/data/alphahunter.db'); d=sqlite3.connect(" + repr(backup) + "); s.backup(d); d.close()")
run('docker', 'volume', 'create', volume)
env = os.environ.copy()
env['SECRET_KEY'] = secrets.token_hex(32)
try:
    run('docker', 'run', '-d', '--name', name, '--network', 'none', '--memory', '384m',
        '-v', volume + ':/data', '-e', 'SECRET_KEY', '-e', 'APP_ENV=production',
        '-e', 'PRIVATE_UI_NO_AUTH=true', '-e', 'PUBLIC_ORIGIN=https://restore.ts.net',
        '--entrypoint', 'python', image, '-c', 'import time; time.sleep(600)', env=env)
    source = subprocess.Popen(['docker', 'exec', 'alphahunter', 'python', '-c',
        'import sys; sys.stdout.buffer.write(open(' + repr(backup) + ',"rb").read())'], stdout=subprocess.PIPE)
    subprocess.run(['docker', 'exec', '-i', name, 'python', '-c',
        'import sys; open("/data/alphahunter.db","wb").write(sys.stdin.buffer.read())'], stdin=source.stdout, check=True)
    source.stdout.close()
    assert source.wait() == 0
    probe = '''import json,sqlite3
from fastapi.testclient import TestClient
from app.main import app
from app.config import get_settings
from app.db import init_db
init_db()
c=sqlite3.connect('/data/alphahunter.db'); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
names=['paper_accounts','paper_positions','paper_trades','opportunities','wallets']
counts={n:c.execute('select count(*) from '+n).fetchone()[0] for n in names}
s=get_settings(); assert s.trading_mode=='paper' and not s.enable_live_execution
# Do not enter lifespan: restored data must not trigger worker activity.
client=TestClient(app)
for p in ['dashboard','positions','opportunities','smart-money','trades','learning','health','settings']:
 r=client.get('/app/'+p); assert r.status_code==200 and 'PAPER SYSTEM' in r.text,p
assert client.get('/dashboard/summary').status_code==401
print(json.dumps({'status':'PASS','sqlite':'ok','pages':8,'api':401,'counts':counts}))'''
    print(run('docker', 'exec', name, 'python', '-c', probe))
    print(json.dumps({'container': name, 'volume': volume, 'backup': backup, 'network': 'none'}))
finally:
    subprocess.run(['docker', 'stop', name], stdout=subprocess.DEVNULL, check=False)
