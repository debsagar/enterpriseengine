"""Explicit integration hooks; importing this module never runs the simulation."""
import json
import hashlib
import subprocess
import urllib.request
from pathlib import Path
from company import Stack, project, save

RUN = Path(__file__).parent / 'recurring-run'


def stack_for(run=RUN):
    s = Stack(run)
    s.project = project(run)
    return s


def identities(engine, roster):
    """After seed: roster is topologically ordered, with role/user/name/manager."""
    result = {}
    for r in roster:
        if not engine.admin.list('User', {'name': r['user']}):
            engine.admin.create('User', {'email': r['user'], 'first_name': r['name'],
                                        'send_welcome_email': 0})
        found = engine.admin.list('Employee', {'user_id': r['user']})
        data = {'first_name': r['name'], 'company': engine.company,
                'user_id': r['user'], 'company_email':r.get('mail',r['user']), 'gender': r.get('gender', 'Other'),
                'date_of_birth': r.get('date_of_birth', '1990-01-01'),
                'date_of_joining': '2027-01-01', 'status': 'Active'}
        if r.get('manager'):
            data['reports_to'] = result[r['manager']]['employee']
        employee = (engine.admin.update('Employee', found[0]['name'], data) if found
                    else engine.admin.create('Employee', data))
        result[r['role']] = {'employee': employee['name'], 'user': r['user'],
                             'manager': r.get('manager')}
    p = engine.run / 'private/identity-crosswalk.json'
    save(p, result)
    p.chmod(0o600)
    return result


def operation_bom(engine, item, minutes_per_unit=0.1):
    """Before any Work Order: create a routed replacement BOM, retain old BOM."""
    if 'v2-bom:'+item in engine.docs:return engine.get('v2-bom:'+item)
    engine.write('v2-operation', 'Operation', {'name': 'Fill and pack', 'operation': 'Fill and pack'})
    engine.write('v2-workstation', 'Workstation', {'workstation_name': 'Filler F1'})
    old = engine.get('bom:' + item)
    return engine.write('v2-bom:' + item, 'BOM', {
        'item': item, 'quantity': 1, 'company': engine.company, 'currency': 'EUR',
        'with_operations': 1, 'items': [{k: r[k] for k in ('item_code', 'qty', 'rate')} for r in old['items']],
        'operations': [{'operation': 'Fill and pack', 'workstation': 'Filler F1',
                        'time_in_mins': minutes_per_unit}]}, submit=True)


def job_cards(engine, work_order):
    """After routed WO submit, before manufacture: return native generated cards."""
    cards = engine.admin.list('Job Card', {'work_order': work_order}, limit=1000)
    if not cards:
        raise RuntimeError('Work Order has no operation-backed Job Cards')
    return [engine.admin.get('Job Card', c['name']) for c in cards]


def complete_job(engine, name, employee, start, end, quantity):
    """Caller supplies actual modeled labor interval; no fabricated elapsed time."""
    if end <= start or quantity <= 0:
        raise ValueError('Positive labor interval and quantity required')
    api=engine.api['PRODUCTION']
    card = api.get('Job Card', name)
    if card['docstatus'] == 1:
        return card
    api.update('Job Card', name, {'time_logs': [{'employee': employee,
        'from_time': start, 'to_time': end, 'completed_qty': quantity}]})
    return api.update('Job Card', name, {'docstatus': 1})


class Tickets:
    """Gitea Issues REST API; credentials loaded privately, records in Gitea DB."""
    def __init__(self, run=RUN):
        self.run = Path(run)
        self.config = json.loads((self.run / 'env/tickets.json').read_text())
        from norvale.environment import docker
        self.config['url']='http://'+docker.container_ip(project(self.run),'tickets')+':3000'

    def request(self, method, path, body=None):
        req = urllib.request.Request(self.config['url'] + '/api/v1' + path,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={'Authorization': 'token ' + self.config['token'], 'Content-Type': 'application/json'})
        from router import request_json
        return request_json(req)

    def records(self):
        result = []
        page = 1
        while True:
            rows = self.request('GET', f'/repos/controller/operations/issues?state=all&limit=50&page={page}')
            result.extend(rows)
            if len(rows) < 50:
                return result
            page += 1

    def open(self, key, title, body):
        marker = '<!-- reference:' + hashlib.sha256(key.encode()).hexdigest() + ' -->'
        for row in self.records():
            if marker in (row.get('body') or ''):
                return row
        return self.request('POST', '/repos/controller/operations/issues',
                            {'title': title, 'body': marker + '\n' + body})

    def close(self, number, resolution):
        comments=self.request('GET',f'/repos/controller/operations/issues/{number}/comments')
        if not any(r['body']==resolution for r in comments):self.request('POST', f'/repos/controller/operations/issues/{number}/comments', {'body': resolution})
        return self.request('PATCH', f'/repos/controller/operations/issues/{number}', {'state': 'closed'})


def export_native(run=RUN):
    """Privileged DB snapshot, separate mail archive and API ticket export."""
    s = stack_for(run)
    out = Path(run) / 'export'
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    from norvale.environment import docker
    with (out / 'erp.sql').open('w') as f:
        subprocess.run(['docker','compose','-p',s.project,'-f',str(s.file),'exec','-T',
                        'db','mariadb-dump','-uroot','-padmin','--single-transaction',s.db_name()],
                       stdout=f, stderr=subprocess.PIPE, check=True)
    with (out / 'mail.tar').open('wb') as f:
        subprocess.run(['docker','compose','-p',s.project,'-f',str(s.file),'exec','-T',
                        'mail','tar','cf','-','/var/mail'], stdout=f, stderr=subprocess.PIPE, check=True)
    t = Tickets(run)
    issues = t.records()
    for issue in issues:
        issue['exported_comments'] = t.request('GET',f'/repos/controller/operations/issues/{issue["number"]}/comments')
    save(out / 'tickets.json', issues)
    private = Path(run) / 'private'
    private.mkdir(exist_ok=True)
    private.chmod(0o700)
    save(private / 'system-crosswalk.json', {'tickets': {str(i['number']): i['html_url'] for i in issues}, 'identity_file': 'identity-crosswalk.json', 'simulation_started': bool(json.loads((Path(run)/'state.json').read_text()).get('completed')) if (Path(run)/'state.json').exists() else False})
    (private / 'system-crosswalk.json').chmod(0o600)
    for p in out.iterdir():p.chmod(0o700 if p.is_dir() else 0o600)
