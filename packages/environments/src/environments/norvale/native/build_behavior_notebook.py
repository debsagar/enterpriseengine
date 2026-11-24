"""Build and execute a short, read-only notebook over the actual safe exports."""
import argparse
from environments.norvale.paths import CHECKOUT
import os
from pathlib import Path
import nbformat as n
from nbclient import NotebookClient
from nbconvert import HTMLExporter

def main():
 p=argparse.ArgumentParser();p.add_argument('--run',type=Path,default=CHECKOUT/'data');p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 run=a.run.resolve();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
 cells=[]
 def md(s):cells.append(n.v4.new_markdown_cell(s))
 def code(s):cells.append(n.v4.new_code_cell(s))
 md('# Norvale: watch people work in three systems\nRun all cells to inspect the completed January–April 2027 company. These cells only read saved exports. They never start services, generate activity, or call a model.')
 code('''from pathlib import Path
import json, html
from IPython.display import display, HTML
EXPORT = Path('''+repr(os.path.relpath(run/'observable', out))+''')
records = json.loads((EXPORT / 'native-records.json').read_text())
tickets = json.loads((EXPORT / 'tickets.json').read_text())
mail = json.loads((EXPORT / 'mail.json').read_text())
def rows(kind): return list(records.get(kind, {}).values())
def show(data):
    columns = list(data[0]) if data else []
    esc = lambda value: html.escape(str(value))
    display(HTML('<table><tr>' + ''.join('<th>'+esc(c)+'</th>' for c in columns) + '</tr>' + ''.join('<tr>'+''.join('<td>'+esc(r[c])+'</td>' for c in columns)+'</tr>' for r in data)+'</table>'))
show([{'Software': 'ERPNext', 'Storage': 'MariaDB', 'Saved records': sum(map(len, records.values()))},
      {'Software': 'Gitea', 'Storage': 'Separate SQLite', 'Saved records': len(tickets)},
      {'Software': 'Email', 'Storage': 'Mail files / SMTP + IMAP', 'Saved records': len(mail)}])''')
 md('**What you see:** real saved records from three applications. Manufacturing and customer sales are inside ERPNext. There is no separate MES or CRM application in this run.')
 code('''show([{'Person': r.get('employee_name'), 'ERP user': r.get('user_id'), 'Reports to': r.get('reports_to')} for r in rows('Employee')[:6]])''')
 md('**People:** these are seeded employees linked to software users. Role permissions control the ERP records each role can read or change. The reporting link is an Employee ID.')
 code('''# Choose another order here and rerun this cell to follow its stored links.
ORDER = 'SAL-ORD-2027-00001'
order = records['Sales Order'][ORDER]
work = next((r for r in rows('Work Order') if r.get('sales_order') == ORDER), None)
card = next((r for r in rows('Job Card') if work and r.get('work_order') == work['name']), None)
delivery = next((r for r in rows('Delivery Note') if any(i.get('against_sales_order') == ORDER for i in r.get('items', []))), None)
invoice = next((r for r in rows('Sales Invoice') if delivery and any(i.get('delivery_note') == delivery['name'] for i in r.get('items', []))), None)
payment = next((r for r in rows('Payment Entry') if invoice and any(i.get('reference_name') == invoice['name'] for i in r.get('references', []))), None)
show([{'Role': role, 'ERP record': doc['name'] if doc else 'No linked record', 'What was recorded': value}
      for role, doc, value in [
          ('Sales', order, order.get('customer')),
          ('Planner', work, str(work.get('qty'))+' units' if work else ''),
          ('Production', card, str(card.get('total_completed_qty'))+' units; '+str(card.get('total_time_in_mins'))+' minutes' if card else ''),
          ('Warehouse', delivery, delivery.get('posting_date') if delivery else ''),
          ('Finance — invoice', invoice, invoice.get('grand_total') if invoice else ''),
          ('Finance — payment', payment, payment.get('paid_amount') if payment else '')]])''')
 md('**One order:** sales records the request → planning creates a job → production records work → warehouse ships → finance bills and records payment. The joins above use native links, not matching row numbers. Missing links stay visible.')
 code('''monthly = []
for month in ['2027-01', '2027-02', '2027-03', '2027-04']:
    ordered = sum(float(i.get('qty', 0)) for r in rows('Sales Order') if str(r.get('transaction_date', '')).startswith(month) for i in r.get('items', []))
    made = sum(float(r.get('total_completed_qty', 0)) for r in rows('Job Card') if str(r.get('posting_date', '')).startswith(month) and r.get('docstatus') == 1)
    shipped = sum(float(i.get('qty', 0)) for r in rows('Delivery Note') if str(r.get('posting_date', '')).startswith(month) and r.get('docstatus') == 1 for i in r.get('items', []))
    monthly.append({'Month': month, 'Ordered': ordered, 'Made': made, 'Shipped': shipped})
show(monthly)
maximum = max(r['Ordered'] for r in monthly)
bars = ''.join('<text x="0" y="'+str(28+idx*65)+'">'+r['Month']+'</text><rect x="95" y="'+str(12+idx*65)+'" width="'+str(r['Ordered']/maximum*400)+'" height="15" fill="#a81c27"/><rect x="95" y="'+str(30+idx*65)+'" width="'+str(r['Shipped']/maximum*400)+'" height="15" fill="#777"/>' for idx,r in enumerate(monthly))
display(HTML('<svg viewBox="0 0 560 275" role="img" aria-label="Ordered red, shipped gray, by month" style="max-width:700px;width:100%">'+bars+'</svg>'))''')
 md('**Four months:** red is ordered; gray is shipped. These totals come directly from native business dates. Orders, completed work and shipments happen on different dates; open demand remains at April 30.')
 code('''ticket = sorted(tickets, key=lambda t: t['number'])[0]
show([{'Software': 'Gitea', 'Ticket': ticket['number'], 'Title': ticket['title'], 'State now': ticket['state']}])
# Strip private deduplication comments; display the business description and resolution.
import re
display(HTML('<pre style="white-space:pre-wrap">'+html.escape(re.sub(r'<!--.*?-->', '', ticket['body'], flags=re.S).replace('\\\\n','\\n').strip())+'</pre>'))
for comment in ticket.get('comments', []):
    print(comment['body'])''')
 md('**Maintenance:** a separate ticket service records the equipment problem and resolution. Its text carries the simulated dates and the ERP task ID; server timestamps use the real clock.')
 code('''message = mail[0]['message']
show([{'Software': 'Email', 'From': message['from'], 'To': message['to'], 'Subject': message['subject'], 'Business date': message['date']}])
print(message['body'].strip())
po = re.search(r'PUR-ORD-\\d{4}-\\d+', message['body'])
if po and po.group() in records.get('Purchase Order', {}):
    purchase = records['Purchase Order'][po.group()]
    show([{'ERP purchase': purchase['name'], 'Supplier': purchase['supplier'], 'Total': purchase['grand_total'], 'Status at cutoff': purchase['status']}])''')
 md('**Buyer → manager:** this delivered email asks for approval and names an ERP purchase. Selected judgment cases use JEV to decide whether to request an LLM decision. Routine bookings run in code. JEV coverage was corrected February 4; this trajectory recorded no standing-policy executions.\n\n**Scope:** these records support inspection of linked events. An extraction benchmark has not been run; private runtime references are excluded from this release.')
 nb=n.v4.new_notebook(cells=cells,metadata={'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'}})
 NotebookClient(nb,timeout=90,kernel_name='python3').execute(cwd=str(out))
 n.write(nb,out/'Behavior.ipynb')
 exporter=HTMLExporter();exporter.exclude_input=True
 content,_=exporter.from_notebook_node(nb)
 import re
 content=re.sub(r'(\d+(?:\.\d+)?)vh',lambda m:str(float(m[1])*8)+'px',content)
 (out/'Behavior.html').write_text(content)
 assert sum(c.cell_type=='code' and bool(c.outputs) for c in nb.cells)==6
 print('Executed 6 read-only data cells; saved notebook and rendered HTML:',out)
if __name__=='__main__':main()
