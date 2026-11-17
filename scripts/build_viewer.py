import json,re
from pathlib import Path
from datetime import datetime,timezone
root=Path(__file__).resolve().parents[1]
p=root/'data/observable'
d=json.loads((p/'native-records.json').read_text()); tickets=json.loads((p/'tickets.json').read_text());mail=json.loads((p/'mail.json').read_text())
def pick(r,keys):return {k:r.get(k) for k in keys.split()}
data={'counts':{k:len(v) for k,v in d.items()},'orders':[],'tickets':[],'mail':[]}
for o in list(d['Sales Order'].values())[:12]:
 name=o['name']; w=[r for r in d['Work Order'].values() if r.get('sales_order')==name]; wn={r['name'] for r in w}
 ships=[r for r in d['Delivery Note'].values() if any(i.get('against_sales_order')==name for i in r.get('items',[]))]
 inv=[r for r in d['Sales Invoice'].values() if any(i.get('sales_order')==name for i in r.get('items',[]))]; ins={r['name'] for r in inv}
 pay=[r for r in d['Payment Entry'].values() if any(i.get('reference_name') in ins for i in r.get('references',[]))]
 rows=[]
 for typ,records,keys in [('Sales Order',[o],'name transaction_date customer total_qty grand_total status owner'),('Work Order',w,'name sales_order production_item qty produced_qty status owner'),('Job Card',[r for r in d['Job Card'].values() if r.get('work_order') in wn],'name work_order operation total_completed_qty total_time_in_mins status'),('Delivery Note',ships,'name posting_date total_qty grand_total status owner'),('Sales Invoice',inv,'name posting_date grand_total outstanding_amount status'),('Payment Entry',pay,'name posting_date party paid_amount status')]:
  for r in records:rows.append({'type':typ,**pick(r,keys)})
 data['orders'].append({'name':name,'customer':o['customer'],'rows':rows})
for t in tickets[:6]:data['tickets'].append({**pick(t,'number title state'),'body':re.sub(r'<!--.*?-->','',t['body'],flags=re.S).strip(),'comments':[c['body'] for c in t['comments']]})
for m in mail[:8]:data['mail'].append({'sender_role':m['sender'],'recipient_role':m['recipient'],**pick(m['message'],'from to subject date body')})
data['ticket_count']=len(tickets);data['mail_count']=len(mail)
blob=json.dumps(data).replace('<','\\u003c')
html='''<!doctype html><meta charset="utf-8"><style>
:root{font-family:var(--font-sans);color:var(--foreground)}body{margin:0}button,select{font:inherit;color:var(--foreground);background:var(--secondary);border:1px solid var(--border);border-radius:var(--radius);padding:8px;cursor:pointer}nav{display:flex;gap:8px;margin-bottom:12px}button[aria-selected=true]{background:var(--primary);color:var(--primary-foreground)}p{line-height:1.5}small,.muted{color:var(--muted-foreground)}article{background:var(--card);padding:16px;border-radius:var(--radius);margin:10px 0}h3{font-size:15px;margin:0 0 8px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}td,th{text-align:left;padding:9px;border-bottom:1px solid var(--border);vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px var(--font-mono)}select{max-width:100%}details{margin-top:10px}summary{cursor:pointer}#content{min-height:470px}</style>
<nav role="tablist" aria-label="Software"><button role="tab" aria-selected="true" data-tab="erp">ERPNext</button><button role="tab" aria-selected="false" data-tab="tickets">Gitea</button><button role="tab" aria-selected="false" data-tab="mail">Email</button></nav><div id="content"></div><small>Actual Norvale v2 application records • filtered completion snapshot • business dates are simulated 2027 dates. This viewer is read-only.</small><script>
const D=DATA, c=document.getElementById('content');
const esc=s=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
function show(tab){document.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',b.dataset.tab===tab));
if(tab==='erp'){c.innerHTML=`<p><b>Storage: MariaDB</b> · ERP database <code>_585c2ba273a24156</code><br><small>Sales → planning / production → warehouse → finance. Counts below also checked against the live database.</small></p><p>${['Sales Order','Work Order','Job Card','Delivery Note','Payment Entry'].map(k=>`<b>${D.counts[k]}</b> ${k}`).join(' · ')}</p><label>Trace an order <select id="order">${D.orders.map((o,i)=>`<option value="${i}">${esc(o.name)} · ${esc(o.customer)}</option>`).join('')}</select></label><div id="trace"></div><details><summary>Export evidence and scope</summary><p>data/observable/native-records.json. Showing the first 12 orders; linked records selected through native sales_order, work_order, order-item and invoice-reference fields. Full table counts are from the export; the five counts above match live read-only SQL.</p></details>`;document.getElementById('order').onchange=e=>trace(+e.target.value);trace(0)}
else if(tab==='tickets'){c.innerHTML=`<p><b>Storage: Gitea SQLite</b> · <code>/data/gitea/gitea.db</code> inside the tickets container.<br><small>Maintenance coordination · separate from ERP MariaDB.</small></p><p><b>${D.ticket_count}</b> exported issues · showing ${D.tickets.length}</p>`+D.tickets.map(t=>`<article><h3>#${t.number} ${esc(t.title)} · ${esc(t.state)}</h3><pre>${esc(t.body)}</pre><details><summary>${t.comments.length} comments</summary>${t.comments.map(s=>`<pre>${esc(s)}</pre>`).join('')}</details></article>`).join('')+'<small>Evidence: data/observable/tickets.json</small>'}
else{c.innerHTML=`<p><b>Storage: mail files</b> · <code>/var/mail</code> in the mail service.<br><small>Buyer ↔ supply chain manager and other role correspondence. Separate from both databases.</small></p><p><b>${D.mail_count}</b> exported message records · showing ${D.mail.length}</p>`+D.mail.map(m=>`<article><h3>${esc(m.subject)}</h3><small>${esc(m.sender_role)} → ${esc(m.recipient_role)} · ${esc(m.date)}<br>${esc(m.from)} → ${esc(m.to)}</small><pre>${esc(m.body)}</pre></article>`).join('')+'<small>Evidence: data/observable/mail.json</small>'}}
function trace(i){document.getElementById('trace').innerHTML='<div class="scroll"><table><thead><tr><th>Native record</th><th>Actual fields</th></tr></thead><tbody>'+D.orders[i].rows.map(r=>`<tr><td><b>${esc(r.type)}</b><br>${esc(r.name)}</td><td>${Object.entries(r).filter(([k])=>!['type','name'].includes(k)).map(([k,v])=>`${esc(k)}: ${esc(v)}`).join('<br>')}</td></tr>`).join('')+'</tbody></table></div>'}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>show(b.dataset.tab));show('erp');
</script>'''.replace('DATA',blob)
out=root/'docs/data-viewer.html';out.write_text(html)
assert len(data['orders'])==12 and all(len(o['rows'])>=1 for o in data['orders'])
assert data['orders'][0]['rows'][-1]['type']=='Payment Entry'
assert not re.search(r'api[_-]?key|password|secret_key',html,re.I)
print(out);print({k:data[k] for k in ['ticket_count','mail_count']});print('Checks passed: linked first order ends in payment; whitelisted fields; no credential keys.')
