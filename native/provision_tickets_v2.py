"""Provision the isolated real Gitea application; no simulation events."""
import json, secrets, subprocess, time, urllib.request
from systems_v2 import RUN, stack_for, save
from norvale.environment import docker
import sys
from pathlib import Path
if len(sys.argv)>1:RUN=Path(sys.argv[1]).resolve()
s = stack_for(RUN)
c = json.loads(s.file.read_text())
c['services']['tickets'] = {'image':'gitea/gitea:1.22.6','restart':'unless-stopped',
    'networks':['office'], 'volumes':['ticket-data:/data'],
    'environment':{'GITEA__security__INSTALL_LOCK':'true','GITEA__database__DB_TYPE':'sqlite3',
                   'GITEA__service__DISABLE_REGISTRATION':'true'}}
c['volumes']['ticket-data'] = {}
s.file.write_text(json.dumps(c,indent=2))
s.compose('up','-d','tickets',capture_output=True)
url = 'http://' + docker.container_ip(s.project,'tickets') + ':3000'
for attempt in range(120):
    try:
        urllib.request.urlopen(url+'/api/v1/version', timeout=2)
        break
    except OSError: time.sleep(1)
else: raise RuntimeError('Gitea unavailable')
p = RUN/'env/tickets.json'
if not p.exists():
    password = secrets.token_urlsafe(32)
    r = s.compose('exec','-T','--user','git','tickets','gitea','admin','user','create',
        '--username','controller','--password',password,'--email','controller@norvale.test',
        '--admin','--must-change-password=false',capture_output=True,text=True)
    r = s.compose('exec','-T','--user','git','tickets','gitea','admin','user','generate-access-token',
        '--username','controller','--token-name','native-v2','--scopes','all','--raw',capture_output=True,text=True)
    save(p,{'url':url,'token':r.stdout.strip()});p.chmod(0o600)
from systems_v2 import Tickets
t = Tickets(RUN)
try:t.request('GET','/repos/controller/operations')
except Exception:t.request('POST','/user/repos',{'name':'operations','private':True,'has_issues':True})
issue=t.open('integration-smoke','Integration connectivity check','Scripted integration check; no simulated work performed.')
assert t.close(issue['number'],'Native API persistence verified')['state']=='closed'
save(RUN/'ticket-check.json',{'application':'Gitea','issue':issue['number'],'state':'closed'})
print('Gitea native issue persisted and closed')
