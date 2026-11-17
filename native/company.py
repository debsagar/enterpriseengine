"""Native Norvale engine. One durable event sequence for notebook and batch."""
import sys, json, hashlib, time, os, random, math, fcntl
from pathlib import Path
from datetime import datetime, timedelta, timezone
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'walkthrough'))
from norvale.environment.stack import Stack
from norvale.environment import compose as compose_mod
from norvale.loader.erp_client import ErpClient, ErpError
from engine import EnterpriseSimV4, ROLES

RUN = Path(__file__).parent/'run'  # preserved fixed-scenario evidence
ACTIVE_RUN = Path(__file__).parent/'recurring-run'

def observable(value):
    if isinstance(value, dict):return {k:observable(v) for k,v in value.items() if k!='custom_norvale_operation'}
    if isinstance(value, list):return [observable(v) for v in value]
    return value

def save(path, value):
    path=Path(path); value=observable(value) if 'observable' in path.parts else value; path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(value, indent=2, default=str)); tmp.replace(path)

def project(run):
    return 'norvale-generation-dev' if Path(run).resolve()==RUN.resolve() else 'nvg-'+hashlib.sha256(str(Path(run).resolve()).encode()).hexdigest()[:12]

def provision(run=ACTIVE_RUN):
    stack=Stack(run); stack.project=project(run)
    original=compose_mod.build
    def build(*a, **kw):
        c=original(*a, **kw)
        c['services']['db']['command'].append('--innodb-fatal-semaphore-wait-threshold=4294967295')
        return c
    compose_mod.build=build
    ids=[{'id':r['role_id'], 'mail':r['email'].replace('.example','.test'), 'chat':r['role_id'].lower()} for r in ROLES]
    try:stack.up(ids, datetime(2027,1,1,tzinfo=timezone.utc))
    finally:compose_mod.build=original
    return stack


class Engine:
    company='Norvale Consumer Care BV'
    abbr='NCC'
    def __init__(self, run=ACTIVE_RUN):
        self.run=Path(run); self.stack=Stack(self.run); self.stack.project=project(self.run)
        self.eps=self.stack.endpoints(); self.admin=ErpClient(self.eps['erp'],self.eps['site']); self.admin.login('Administrator','admin')
        token_path=self.run/'env/controller-token.json'
        if token_path.exists():self.admin.token=json.loads(token_path.read_text())['token']
        else:
            secret=self.admin.call('frappe.core.doctype.user.user.generate_keys',{'user':'Administrator'})['api_secret']
            self.admin.token=self.admin.get('User','Administrator')['api_key']+':'+secret
            save(token_path,{'token':self.admin.token});token_path.chmod(0o600)
        self.config=json.loads((self.run/'config.json').read_text()) if (self.run/'config.json').exists() else None
        if self.config:self.validate_config(self.config)
        self.catalog=EnterpriseSimV4(seed=self.config['seed'] if self.config else 42, days=30, mode='dry-run')
        self.state=json.loads((self.run/'state.json').read_text()) if (self.run/'state.json').exists() else {'docs':{},'completed':[], 'physical':{}, 'decisions':{}, 'day':0}
        self.docs=self.state['docs']; self.api={}; self.event='setup'; self.day=self.state['day']
        self.wh={k:f'{v} - NCC' for k,v in [('rm','Raw Materials'),('fg','Finished Goods'),('wip','Work In Progress'),('hold','Quarantine')]}
        self.roles={'BUYER':['Purchase User'],'SCM_MGR':['Purchase User','Purchase Manager'],'SALES':['Sales User'],'WAREHOUSE':['Stock User'],'PLANNER':['Manufacturing User','Stock User'],'PRODUCTION':['Manufacturing User','Stock User'],'QA':['Quality Manager','Stock User'],'FINANCE':['Accounts User','Accounts Manager'],'MAINTENANCE':['Maintenance User'],'IT_ADMIN':['System Manager','Item Manager'],'CFO':['Accounts Manager','Purchase Manager']}
        secpath=self.run/'env/role-tokens.json'
        if secpath.exists():
            self.tokens=json.loads(secpath.read_text()); self.connect()
        else:self.tokens={}
        self.log('controller_started',source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())

    def connect(self):
        self.api={r:ErpClient(self.eps['erp'],self.eps['site'],t) for r,t in self.tokens.items()}

    def log(self,kind,**data):
        self.run.mkdir(parents=True, exist_ok=True)
        trace=self.run/'trace.jsonl';trace.touch(mode=0o600, exist_ok=True);trace.chmod(0o600)
        with trace.open('a') as f:f.write(json.dumps({'event':self.event,'day':self.day,'kind':kind,**data},default=str)+'\n')

    def persist(self):
        self.state['day']=self.day;save(self.run/'state.json',self.state)

    def write(self,key,dt,data,role=None,submit=False):
        api=self.api[role] if role else self.admin
        mutated=False
        if key in self.docs:
            saved=self.docs[key]; assert saved['doctype']==dt
            doc=api.get(dt,saved['name'])
        else:
            if role and self.admin.list('Custom Field',{'dt':dt,'fieldname':'custom_norvale_operation'}):
                existing=api.list(dt,{'custom_norvale_operation':hashlib.sha256(key.encode()).hexdigest()})
                if existing:
                    self.docs[key]={'doctype':dt,'name':existing[0]['name']};self.persist()
                    return self.write(key,dt,data,role,submit)
                data={**data,'custom_norvale_operation':hashlib.sha256(key.encode()).hexdigest()}
            self.log('request',role=role or 'setup administrator',method='POST',doctype=dt,body=data)
            try:doc=api.create(dt,data);mutated=True
            except Exception as e:self.log('rejected',error=str(e));raise
            self.docs[key]={'doctype':dt,'name':doc['name']};self.persist()
            self.log('created',role=role,doc=doc)
        if submit and doc['docstatus']==0:
            self.log('request',role=role,method='PUT',doctype=dt,name=doc['name'],body={'docstatus':1})
            doc=api.update(dt,doc['name'],{'docstatus':1});mutated=True
        actual=api.get(dt,doc['name']) if mutated else doc
        if submit:assert actual['docstatus']==1
        self.log('readback',role=role,doc=actual)
        return actual

    def get(self,key,role=None):
        d=self.docs[key];return (self.api[role] if role else self.admin).get(d['doctype'],d['name'])

    def mapped(self,role,method,existing=None,**args):
        # Recover before asking ERP to map a source that may already be fully consumed.
        if existing:
            key,dt=existing
            if key not in self.docs:
                found=self.api[role].list(dt,{'custom_norvale_operation':hashlib.sha256(key.encode()).hexdigest()})
                if found:self.docs[key]={'doctype':dt,'name':found[0]['name']};self.persist()
            if key in self.docs:return self.get(key,role)
        self.log('mapper_request',role=role,method=method,args=args)
        doc=self.api[role].call(method,args)
        self.log('mapper_response',role=role,response=doc);return doc

    def seed(self):
        if not self.admin.list('Company',{'name':self.company}):
            self.admin.call('frappe.desk.page.setup_wizard.setup_wizard.setup_complete',{'args':{'language':'English','full_name':'ERP Admin','email':'it@norvale.test','password':'Admin!2026x','chart_of_accounts':'Standard','setup_demo':0,'company_name':self.company,'company_abbr':self.abbr,'country':'Netherlands','currency':'EUR','timezone':'UTC','fy_start_date':'2027-01-01','fy_end_date':'2027-12-31'}})
            self.admin.login('Administrator','admin')
        if not self.admin.list('Fiscal Year',{'name':'2027'}):self.admin.create('Fiscal Year',{'year':'2027','year_start_date':'2027-01-01','year_end_date':'2027-12-31'})
        for role,perms in self.roles.items():
            email=role.lower()+'@norvale.test'
            if not self.admin.list('User',{'name':email}):self.admin.create('User',{'email':email,'first_name':role,'enabled':1,'send_welcome_email':0,'roles':[{'role':r} for r in perms]})
            if role not in self.tokens:
                secret=self.admin.call('frappe.core.doctype.user.user.generate_keys',{'user':email})['api_secret'];self.tokens[role]=self.admin.get('User',email)['api_key']+':'+secret
                save(self.run/'env/role-tokens.json',self.tokens);(self.run/'env/role-tokens.json').chmod(0o600)
        self.connect()
        for k,name in self.wh.items():
            if not self.admin.list('Warehouse',{'name':name}):self.admin.create('Warehouse',{'warehouse_name':name.split(' - ')[0],'company':self.company})
        self.admin.update('Stock Settings','Stock Settings',{'allow_negative_stock':0,'allow_to_make_quality_inspection_after_purchase_or_delivery':1})
        for c in self.catalog.accounts.values():self.write('customer:'+c['account_id'],'Customer',{'customer_name':c['name'],'customer_type':'Company','customer_group':'Commercial','territory':'All Territories','credit_limits':[{'company':self.company,'credit_limit':c['credit_limit_eur']}]})
        for k,v in self.catalog.vendors.items():self.write('vendor:'+k,'Supplier',{'supplier_name':v['name'],'supplier_group':'All Supplier Groups','supplier_type':'Company'})
        for code,m in self.catalog.materials.items():
            self.write('item:'+code,'Item',{'item_code':code,'item_name':m['name'],'item_group':'Raw Material','stock_uom':'Kg' if m['uom']=='KG' else 'Nos','is_stock_item':1,'valuation_rate':m['std_cost']})
        # Explicit water closes V4's otherwise implicit recipe mass balance.
        self.write('item:WATER','Item',{'item_code':'WATER','item_name':'Process water','item_group':'Raw Material','stock_uom':'Kg','is_stock_item':1,'valuation_rate':0.001})
        for s in self.catalog.skus:
            code=s['sku_id']; self.write('item:'+code,'Item',{'item_code':code,'item_name':s['name'],'item_group':'Products','stock_uom':'Nos','is_stock_item':1})
            ingredients=[{'item_code':k,'qty':q/1000,'rate':self.catalog.materials[k]['std_cost']} for k,q in s['bom'].items()]
            mass=sum(q/1000 for k,q in s['bom'].items() if self.catalog.materials[k]['uom']=='KG')
            ingredients.append({'item_code':'WATER','qty':s['pack_size_kg']-mass,'rate':0.001})
            self.write('bom:'+code,'BOM',{'item':code,'quantity':1,'company':self.company,'currency':'EUR','items':ingredients},submit=True)
        if 'opening' not in self.docs:
            items=[{'item_code':k,'qty':10000 if m['uom']=='KG' else 100000,'t_warehouse':self.wh['rm'],'basic_rate':m['std_cost']} for k,m in self.catalog.materials.items()]
            items.append({'item_code':'WATER','qty':100000,'t_warehouse':self.wh['rm'],'basic_rate':0.001})
            if self.config:
                use=self.usage()
                for row in items:row['qty']=max(1,math.ceil(use.get(row['item_code'],0)*self.config['opening_stock_days']))
                items[-1]['qty']=100000  # process water is supplied by an unconstrained utility tank
            self.write('opening','Stock Entry',{'stock_entry_type':'Material Receipt','company':self.company,'items':items},'WAREHOUSE',True)
        self.persist()

    def decision(self,key,role,evidence,question):
        if key in self.state['decisions']:return self.state['decisions'][key]
        prompt={'role':role,'as_of':self.date(),'question':question,'evidence':evidence,'instruction':'Return JSON with action, reason, message. Do not assume unseen facts. Prior decisions may be mistaken: reassess using current native evidence and explicit operating policies; do not perpetuate unsupported concerns. Action may be approve, defer, reject, investigate. Approval is not implementation. You may propose standing_policy with effective_date, review_date, nonnegative numeric approval_limit and reorder_target, nonempty exact suppliers and items lists. Only SCM_MGR/CFO may authorize it. Ground all values in observed evidence; omit if insufficient.'}
        keyenv=os.environ.get('OPENAI_API_KEY') or os.environ.get('OPENROUTER_API_KEY')
        model_base=None
        if os.environ.get('NORVALE_OFFLINE')=='1':keyenv=None
        if keyenv:
            import urllib.request
            base=model_base or os.environ.get('OPENAI_BASE_URL','https://openrouter.ai/api/v1' if os.environ.get('OPENROUTER_API_KEY') else 'https://api.openai.com/v1')
            model=os.environ.get('NORVALE_MODEL','openai/gpt-4.1-mini' if 'openrouter' in base else 'gpt-4.1-mini')
            payload={'model':model,'messages':[{'role':'user','content':json.dumps(prompt)}],'response_format':{'type':'json_object'},'max_tokens':1600}
            self.log('model_request',model=model,input=prompt)
            req=urllib.request.Request(base+'/chat/completions',json.dumps(payload).encode(),headers={'Authorization':'Bearer '+keyenv,'Content-Type':'application/json'})
            from router import request_json
            raw=request_json(req,lambda attempt:self.log('model_http_attempt',attempt=attempt))
            self.log('model_response',response=raw)
            if raw['choices'][0].get('finish_reason')!='stop':raise RuntimeError('Incomplete model response; raw response retained for diagnosis')
            answer=json.loads(raw['choices'][0]['message']['content']);mode='model'
        else:raise RuntimeError('LLM judgment requires credentials; no approval fallback')
        if answer.get('action') not in ['approve','defer','reject','investigate']:raise ValueError('Invalid decision action')
        if answer.get('standing_policy') is not None:
            try:self.adopt_policy(role,answer['standing_policy'],evidence,key)
            except (ValueError,KeyError,TypeError) as exc:
                answer['policy_rejected']=str(exc);self.log('policy_rejected',role=role,error=str(exc),proposal=answer.pop('standing_policy'))
        result={'mode':mode,'input':prompt,'output':answer};self.state['decisions'][key]=result;self.persist();self.log('decision',**result);return result

    def date(self):return (datetime(2027,1,1)+timedelta(days=self.day)).date().isoformat()

    def configure(self):
        for dt in ['Stock Ledger Entry','Bin']:
            for role in ['Purchase User','Purchase Manager']:
                if not self.admin.list('Custom DocPerm',{'parent':dt,'role':role,'read':1}):
                    result=self.stack.bench('execute','frappe.permissions.add_permission','--kwargs',json.dumps({'doctype':dt,'role':role,'ptype':'read'}))
                    if result.returncode:raise RuntimeError(result.stderr[-500:])
        perms=self.admin.list('Custom DocPerm',{'parent':'Account','role':'Stock User'},['name','select'])
        if not perms:
            r=self.stack.bench('execute','frappe.permissions.add_permission','--kwargs',json.dumps({'doctype':'Account','role':'Stock User','ptype':'select'}))
            if r.returncode:raise RuntimeError(r.stderr[-500:])
        for dt in ['Stock Entry','Sales Order','Purchase Order','Purchase Receipt','Purchase Invoice','Work Order','Quality Inspection','Delivery Note','Sales Invoice','Payment Entry','ToDo','Comment','Journal Entry','Opportunity','Note','Supplier Quotation']:
            if not self.admin.list('Custom Field',{'dt':dt,'fieldname':'custom_norvale_operation'}):
                self.admin.create('Custom Field',{'dt':dt,'fieldname':'custom_norvale_operation','label':'Norvale operation','fieldtype':'Data','unique':1,'no_copy':1,'read_only':1})

    def workflow_setup(self):
        for name in ['Pending Approval','Approved','Rejected']:
            if not self.admin.list('Workflow State',{'name':name}):self.admin.create('Workflow State',{'workflow_state_name':name})
        for name in ['Approve','Reject']:
            if not self.admin.list('Workflow Action Master',{'name':name}):self.admin.create('Workflow Action Master',{'workflow_action_name':name})
        self.write('po-workflow','Workflow',{'workflow_name':'Norvale purchasing approval','document_type':'Purchase Order','is_active':1,'workflow_state_field':'workflow_state','states':[{'state':'Pending Approval','doc_status':'0','allow_edit':'Purchase User'},{'state':'Approved','doc_status':'1','allow_edit':'Purchase Manager'},{'state':'Rejected','doc_status':'0','allow_edit':'Purchase Manager'}],'transitions':[{'state':'Pending Approval','action':'Approve','next_state':'Approved','allowed':'Purchase Manager','allow_self_approval':0},{'state':'Pending Approval','action':'Reject','next_state':'Rejected','allowed':'Purchase Manager','allow_self_approval':0}]})

    def mail(self,key,sender,recipient,subject,body):
        from norvale.agents.comms import Comms
        roster={r['role_id']:r for r in ROLES};sec=json.loads((self.run/'env/secrets.json').read_text())
        def client(role):
            role={'BUYER':'BUYER_CHEM','SCM_MGR':'SC_MGR','WAREHOUSE':'WH_CLERK','MAINTENANCE':'MAINT_LEAD'}.get(role,role)
            r=roster[role];return Comms({'mail':r['email'].replace('.example','.test'),'chat':role.lower()},sec[role],self.eps)
        marker='[Ref '+hashlib.sha256(key.encode()).hexdigest()[:16]+'] ';receiver=client(recipient)
        found=[r for r in receiver.inbox(limit=200) if r['subject']==marker+subject]
        if not found:
            client(sender).send_mail([receiver.addr],marker+subject,body,datetime.fromisoformat(self.date()).replace(tzinfo=timezone.utc))
            self.stack.settle()
            found=[r for r in receiver.inbox(limit=200) if r['subject']==marker+subject]
        if len(found)!=1:raise RuntimeError('Mail delivery/duplicate check failed')
        self.log('native_mail_readback',sender=sender,recipient=recipient,message=receiver.read_mail(found[0]['uid']))

    def purchase(self):
        po=self.write('po','Purchase Order',{'company':self.company,'supplier':self.get('vendor:BRENNER')['name'],'schedule_date':'2027-01-04','items':[{'item_code':'SLES','qty':500,'rate':1.42,'schedule_date':'2027-01-04','warehouse':self.wh['hold']}]},'BUYER')
        self.mail('po-request','BUYER','SCM_MGR','Purchase approval requested',f"Please review {po['name']}; 500 kg SLES at EUR 1.42/kg, due January 4.")

    def approve_purchase(self):
        po=self.get('po','SCM_MGR')
        if po['docstatus']==1:return
        answer=self.decision('purchase','SCM_MGR',[{k:po.get(k) for k in ['name','supplier','grand_total','schedule_date','items']}],'Approve this replenishment purchase or defer/reject/investigate?')
        if answer['output']['action']=='approve':
            self.log('request',role='SCM_MGR',method='POST',path='/api/method/frappe.model.workflow.apply_workflow',body={'doc':po,'action':'Approve'})
            doc=self.api['SCM_MGR'].call('frappe.model.workflow.apply_workflow',{'doc':json.dumps(po),'action':'Approve'})
            assert self.get('po','SCM_MGR')['docstatus']==1;self.log('approved',doc=doc)
        else:self.log('pending',reason=answer['output']);return
        self.mail('po-approved','SCM_MGR','BUYER','Purchase approved',answer['output']['message'])

    def arrive(self):
        if self.get('po')['docstatus']!=1:return
        self.state['physical']['SLES shipment']={'arrived':self.date(),'qty':500,'receipt_booked':False,'inspection':'not yet sampled'};self.persist()
        self.mail('arrival','WAREHOUSE','BUYER','Truck arrived; receipt pending','500 kg SLES at receiving dock. Receipt booking waits for paperwork tomorrow.')

    def receipt(self):
        if 'SLES shipment' not in self.state['physical']:return
        doc=self.mapped('WAREHOUSE','erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt',source_name=self.get('po')['name'])
        for r in doc['items']:r.update(warehouse=self.wh['hold'])
        self.write('receipt','Purchase Receipt',doc,'WAREHOUSE',True)
        self.state['physical']['SLES shipment']['receipt_booked']=True;self.persist()

    def quality(self,key,item,qty,reference,reading=70):
        ref=self.get(reference)
        doc=self.write('qc:'+key,'Quality Inspection',{'inspection_type':'Incoming' if ref['doctype']=='Purchase Receipt' else 'In Process','reference_type':ref['doctype'],'reference_name':ref['name'],'item_code':item,'sample_size':1,'inspected_by':'qa@norvale.test','manual_inspection':1,'status':'Accepted' if 66<=reading<=72 else 'Rejected','readings':[{'specification':'Active percentage' if item=='SLES' else 'Release assay','reading_1':str(reading),'numeric':1,'min_value':66,'max_value':72}]},'QA',True)
        if doc['status']=='Accepted':
            self.write('release:'+key,'Stock Entry',{'company':self.company,'stock_entry_type':'Material Transfer','items':[{'item_code':item,'qty':qty,'s_warehouse':self.wh['hold'],'t_warehouse':self.wh['rm'] if item=='SLES' else self.wh['fg']}]},'QA',True)
        return doc

    def supplier_invoice(self):
        if 'receipt' not in self.docs:return
        doc=self.mapped('FINANCE','erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice',source_name=self.get('receipt')['name'])
        self.write('supplier-invoice','Purchase Invoice',doc,'FINANCE',True)

    def order(self,index):
        s=self.catalog.skus[index];customer=list(self.catalog.accounts.values())[index]
        self.write('order:'+str(index),'Sales Order',{'company':self.company,'customer':self.get('customer:'+customer['account_id'])['name'],'delivery_date':'2027-01-15','items':[{'item_code':s['sku_id'],'qty':240,'rate':s['unit_price'],'delivery_date':'2027-01-15','warehouse':self.wh['fg']}]},'SALES',True)

    def manufacture(self,index):
        if not self.state['physical'].get('Filler F1',{}).get('available',True):
            raise RuntimeError('Filler unavailable: production must wait for technician completion')
        s=self.catalog.skus[index];key=str(index)
        wo=self.write('work:'+key,'Work Order',{'production_item':s['sku_id'],'bom_no':self.get('bom:'+s['sku_id'])['name'],'qty':240,'company':self.company,'use_multi_level_bom':0,'skip_transfer':1,'source_warehouse':self.wh['rm'],'wip_warehouse':self.wh['wip'],'fg_warehouse':self.wh['hold'],'planned_start_date':self.date()+' 08:00:00','sales_order':self.get('order:'+key)['name']},'PLANNER',True)
        doc=self.mapped('PRODUCTION','erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry',work_order_id=wo['name'],purpose='Manufacture',qty=240)
        for r in doc['items']:
            if r.get('is_finished_item'):r['t_warehouse']=self.wh['hold']
            else:r['s_warehouse']=self.wh['rm']
        self.write('manufacture:'+key,'Stock Entry',doc,'PRODUCTION',True)
        self.state['physical']['production:'+key]={'completed':self.date(),'qty':240,'quality':'pending'};self.persist()

    def ship(self,index):
        key=str(index)
        if 'release:product:'+key not in self.docs:
            self.log('blocked',reason='No accepted finished-goods release');return
        doc=self.mapped('WAREHOUSE','erpnext.selling.doctype.sales_order.sales_order.make_delivery_note',source_name=self.get('order:'+key)['name'])
        self.write('delivery:'+key,'Delivery Note',doc,'WAREHOUSE',True)
        self.state['physical']['dispatch:'+key]={'departed':self.date(),'qty':240};self.persist()

    def invoice(self,index):
        key=str(index)
        if 'delivery:'+key not in self.docs:return
        doc=self.mapped('FINANCE','erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice',source_name=self.get('delivery:'+key)['name'])
        self.write('invoice:'+key,'Sales Invoice',doc,'FINANCE',True)

    def pay(self,key,ref):
        if ref not in self.docs:return
        invoice=self.get(ref)
        doc=self.mapped('FINANCE','erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry',existing=(key,'Payment Entry'),dt=invoice['doctype'],dn=invoice['name'],bank_account='Cash - NCC')
        doc.update(reference_no=key,reference_date=self.date())
        self.write(key,'Payment Entry',doc,'FINANCE',True)
        actual=self.get(ref);assert abs(actual['outstanding_amount'])<0.01
        self.log('settled_invoice',doc=actual)

    def maintenance_request(self):
        self.write('maintenance','ToDo',{'description':'Filler F1 vibration observed; inspect before next production release. Completion requires technician report.','allocated_to':'maintenance@norvale.test','date':self.date(),'status':'Open'},'MAINTENANCE')
        self.state['physical']['Filler F1']={'available':False,'fault_observed':self.date()};self.persist()
        self.mail('maintenance','PLANT_MGR','COO','Filler inspection pending','Filler F1 is unavailable. Maintenance owns inspection; no completion has been reported.')

    def maintenance_complete(self):
        doc=self.get('maintenance','MAINTENANCE')
        self.api['MAINTENANCE'].update('ToDo',doc['name'],{'status':'Closed','description':doc['description']+'\nTechnician: loose mount tightened; restart check passed on '+self.date()})
        self.log('maintenance_readback',doc=self.get('maintenance','MAINTENANCE'))
        self.state['physical']['Filler F1']['available']=True;self.persist()

    def change_request(self):
        self.write('change','ToDo',{'description':'Request: raise SLES reorder level to 600 kg in Raw Materials. Buyer requests SCM approval; IT implements after approval.','allocated_to':'scm_mgr@norvale.test','status':'Open'},'BUYER')

    def approve_change(self):
        request=self.get('change','SCM_MGR')
        evidence=[{'request':request['name'],'description':request['description']}, {'purchase':self.get('po','SCM_MGR')['name']}]
        decision=self.decision('master-change','SCM_MGR',evidence,'Approve the proposed reorder level change?')
        if decision['output']['action']=='approve':
            self.write('change-approval','ToDo',{'reference_type':'ToDo','reference_name':request['name'],'allocated_to':'it_admin@norvale.test','description':'APPROVED: SLES reorder level 600 kg in Raw Materials; assigned IT for implementation.'},'SCM_MGR')
        self.mail('change-decision','SCM_MGR','IT_ADMIN','Master data decision',json.dumps(decision['output']))

    def implement_change(self,approval_key='change-approval'):
        if approval_key not in self.docs:raise ValueError('No native approval record exists')
        approval=self.get(approval_key,'IT_ADMIN');request=self.get('change','IT_ADMIN')
        if approval['owner']!='scm_mgr@norvale.test' or approval['reference_name']!=request['name'] or not approval['description'].startswith('APPROVED: SLES reorder level 600 kg'):
            raise ValueError('Approval author, target or approved change does not match')
        doc=self.api['IT_ADMIN'].update('Item','SLES',{'reorder_levels':[{'warehouse':self.wh['rm'],'warehouse_reorder_level':600,'warehouse_reorder_qty':500,'material_request_type':'Purchase'}]})
        self.api['IT_ADMIN'].update('ToDo',request['name'],{'status':'Closed'})
        actual=self.api['IT_ADMIN'].get('Item','SLES')
        assert actual['reorder_levels'][0]['warehouse_reorder_level']==600
        self.api['IT_ADMIN'].update('ToDo',approval['name'],{'status':'Closed'})
        self.log('implemented_change_readback',doc=actual)

    def finance_costs(self, opening=False):
        entries=([('Cash - NCC',50000,0),('Capital Stock - NCC',0,50000)] if opening else
                 [('Salary - NCC',2500,0),('Utility Expenses - NCC',300,0),('Cash - NCC',0,2800)])
        self.write('funding' if opening else 'monthly-costs','Journal Entry',{'company':self.company,'posting_date':self.date(),'voucher_type':'Journal Entry','user_remark':'Development opening capital' if opening else 'Representative period aggregated wages and utilities; no employee payroll module','accounts':[{'account':a,'debit_in_account_currency':d,'credit_in_account_currency':c} for a,d,c in entries]},'FINANCE',True)

    def report(self):
        gl=self.api['FINANCE'].list('GL Entry',{'company':self.company,'posting_date':['<=',self.date()]},['name','posting_date','account','debit','credit','voucher_type','voucher_no'],limit=10000)
        totals={}
        for r in gl:totals[r['account']]=round(totals.get(r['account'],0)+r['debit']-r['credit'],2)
        save(self.run/'native-ledger.json',gl)
        self.write('report','Note',{'title':'January operating and financial review','public':0,'content':'<pre>'+json.dumps({'as_of':self.date(),'native_ledger_rows':len(gl),'account_balances':totals,'pending_work':[r for r in self.admin.list('ToDo',{'status':'Open'},['name','description'])]},indent=2)+'</pre>'},'CFO')
        self.mail('monthly-report','CFO','CEO','January operating review','Native ledger and outstanding work are ready in ERPNext Note '+self.get('report')['name']+'. This is a review request, not a completed meeting.')

    def management_review(self):
        ledger=self.api['CFO'].list('GL Entry',{'company':self.company,'posting_date':['<=',self.date()]},['account','debit','credit','voucher_type','voucher_no'],limit=10000)
        totals={}
        for r in ledger:totals[r['account']]=round(totals.get(r['account'],0)+r['debit']-r['credit'],2)
        evidence={'native_account_balances':totals,'report':self.get('report','CFO')['name'],'report_note':'Opening stock was seeded through a stock receipt. Separate opening setup effects from operating performance. Wages are an aggregate prototype accrual/payment.'}
        outcome=self.decision('finance-review','CFO',evidence,'Review these account balances. Approve the report, investigate discrepancies by assigning Finance, defer for more evidence, or reject it. Do not infer unobserved plant facts.')
        if outcome['output']['action'] in ('investigate','reject','defer'):
            self.write('finance-followup','ToDo',{'description':outcome['output']['reason'],'allocated_to':'finance@norvale.test','date':'2027-02-03','status':'Open'},'CFO')
        self.mail('finance-review','CFO','CEO','CFO review outcome',outcome['output']['message'])

    # Recurring operation: each phase plans from current native records once, then
    # durably executes that plan. Retry never samples new demand or doubles a booking.
    def usage(self):
        use={k:0.0 for k in self.catalog.materials};use['WATER']=0.0
        for s in self.catalog.skus:
            units=s['daily_units']*self.config['demand_scale']*self.config['orders_per_day']/len(self.catalog.skus)
            mass=0
            for code,q in s['bom'].items():
                use[code]+=q/1000*units
                if self.catalog.materials[code]['uom']=='KG':mass+=q/1000
            use['WATER']+=(s['pack_size_kg']-mass)*units
        return use

    def dated(self,day):return (datetime(2027,1,1)+timedelta(days=day)).date().isoformat()

    def observe(self,role,dt,filters,fields):
        rows=self.api[role].list(dt,filters,fields,limit=10000)
        self.log('observation',role=role,doctype=dt,as_of=self.date(),rows=rows)
        return rows

    def inventory(self,role):
        rows=self.observe(role,'Bin',{'warehouse':['in',list(self.wh.values())]},['item_code','warehouse','actual_qty'])
        return {(r['item_code'],r['warehouse']):float(r['actual_qty']) for r in rows}

    def plan(self,make):
        plans=self.state.setdefault('plans',{})
        if self.event not in plans:
            plans[self.event]=make();self.persist();self.log('planned',plan=plans[self.event])
        return plans[self.event]

    def effective_policies(self,role):
        return [{k:v for k,v in p.items() if k!='observed_evidence'} for p in self.state.get('standing_policies',[]) if (p['role']==role or (role=='SCM_MGR' and p['role']=='CFO')) and p['effective_date']<=self.date()<p.get('review_date',self.date())]

    def adopt_policy(self,role,p,evidence,key):
        if role not in ('SCM_MGR','CFO'):raise ValueError('Unauthorized policy author')
        for field in ('approval_limit','reorder_target'):
            if type(p.get(field)) not in (int,float) or not math.isfinite(p[field]) or p[field]<0:raise ValueError('Invalid numeric policy')
        for field in ('suppliers','items'):
            if not isinstance(p.get(field),list) or not p[field] or any(not isinstance(v,str) or not v for v in p[field]):raise ValueError('Invalid policy scope')
        start=datetime.fromisoformat(p['effective_date']).date().isoformat();end=datetime.fromisoformat(p['review_date']).date().isoformat()
        if not self.date()<=start<end:raise ValueError('Invalid policy dates')
        self.write('policy:'+key,'Note',{'title':'Purchasing policy '+hashlib.sha256(key.encode()).hexdigest()[:8]+' effective '+start,'public':1,'content':json.dumps({'authorized_by':role,'effective_date':start,'review_date':end,'policy':p})},role)
        self.state.setdefault('standing_policies',[]).append({**p,'role':role,'effective_date':start,'review_date':end,'source_decision':key})
        self.persist();self.log('policy_authorized',role=role,policy=p,evidence=evidence)

    def choose(self,key,role,evidence,question,action='approve',model=False):
        if key in self.state['decisions']:return self.state['decisions'][key]['output']
        from router import route
        policies=self.effective_policies(role)
        history=[d for d in self.state['decisions'].values() if d['input']['role']==role]
        def relevant(d):
            previous=d['input'].get('evidence',{});previous=previous.get('observations',previous)
            return evidence.get('supplier') is not None and previous.get('supplier')==evidence['supplier']
        cases=[{'as_of':d['input']['as_of'],'mode':d['mode'],'output':{'action':d['output']['action'],'reason':d['output'].get('reason','')[:240]}} for d in history if relevant(d)][-6:]
        matches=[p for p in policies if role=='SCM_MGR' and type(evidence.get('grand_total')) in (int,float) and 0<=evidence['grand_total']<=p['approval_limit'] and evidence.get('supplier') in p['suppliers'] and evidence.get('items') and all(r.get('item_code') in p['items'] and r.get('qty',0)>0 and r.get('rate',-1)>=0 for r in evidence['items'])]
        policy=matches[-1] if matches else None
        def comparable(d):
            previous=d['input'].get('evidence',{})
            before={r['item_code']:r for r in previous.get('items',[])}
            now={r['item_code']:r for r in evidence.get('items',[])}
            def coverage_state(value):
                target=policy['reorder_target']
                return sorted((r['item_code'],target>0 and r.get('released_raw_materials_qty',0)>target,r.get('open_po_qty_excluding_this',0)>0,r.get('quarantine_qty',0)>0) for r in value.get('coverage',[]))
            return coverage_state(previous)==coverage_state(evidence) and bool(now) and now.keys()==before.keys() and all(abs(r['rate']-before[k]['rate'])<=max(.001,abs(before[k]['rate'])*.1) and abs(r['qty']-before[k]['qty'])<=max(1,before[k]['qty']*.25) for k,r in now.items())
        cached=policy and any(d.get('policy')==policy and d.get('classification') is False and comparable(d) for d in history)
        route_evidence={**evidence,'policy_check':{'applicable_policy':False,'authorization':'No matching effective policy; classify this as a new judgment case unless an explicit authorized procedure is supplied.'}}
        route_cases=cases
        if policy:
            route_evidence={**evidence,'policy_check':{'policy_authorizes':'SCM_MGR pre-approves POs from these suppliers for these items with grand_total <= approval_limit between effective_date and review_date','supplier_in_scope':True,'all_items_in_scope':True,'grand_total':evidence['grand_total'],'approval_limit':policy['approval_limit'],'within_dates':True}}
            route_cases=[c for c in cases if c['as_of']>=policy['effective_date']]
        routed=({'judgment':False,'cached':True} if cached else route(route_evidence,route_cases,[policy] if policy else [],lambda attempt:self.log('jev_http_attempt',attempt=attempt)))
        self.log('jev_route',role=role,evidence=evidence,policy=policy,**routed)
        if routed['judgment']:
            return self.decision(key,role,{'observations':evidence,'prior_cases':cases,'effective_policies':policies},question)['output']
        if not policy:
            result={'mode':'pending-policy','input':{'role':role,'as_of':self.date(),'evidence':evidence},'output':{'action':'defer','reason':'Router classified routine, but no authorized rule can execute this case. Request an applicable policy or revised classification.','message':'Pending authorized procedure; no automatic approval.'}}
            self.state['decisions'][key]=result;self.persist();self.log('decision',**result);return result['output']
        result={'mode':'standing-policy','input':{'role':role,'as_of':self.date(),'evidence':evidence},'policy':policy,'classification':False,'output':{'action':'approve','reason':'Within authorized policy scope and review date','message':'Purchase approved under standing policy.'}}
        self.state['decisions'][key]=result;self.persist();self.log('decision',**result)
        return result['output']

    def observed_usage(self):
        rows=self.observe('BUYER','Stock Ledger Entry',{'posting_date':['<=',self.date()],'actual_qty':['<',0],'warehouse':self.wh['rm']},['item_code','actual_qty','posting_date'])
        use={}
        for row in rows:use[row['item_code']]=use.get(row['item_code'],0)-float(row['actual_qty'])/max(1,self.day+1)
        forecast_key='forecast:'+str(self.day)
        if forecast_key in self.docs:
            delivered=self.get(forecast_key,'BUYER');self.log('observation',role='BUYER',doctype='ToDo',as_of=self.date(),rows=[delivered])
            for code,value in json.loads(delivered['description'])['daily_material_forecast'].items():use[code]=max(use.get(code,0),value)
            return use
        forecast={}
        orders=self.observe('PLANNER','Sales Order',{'docstatus':1,'status':['not in',['Closed','Completed','Cancelled']]},['name'])
        for order in orders:
            doc=self.api['PLANNER'].get('Sales Order',order['name'])
            for row in doc['items']:
                remaining=max(0,float(row['qty'])-float(row.get('delivered_qty',0)))
                boms=self.observe('PLANNER','BOM',{'item':row['item_code'],'is_active':1,'is_default':1,'docstatus':1},['name'])
                for bom in boms:
                    recipe=self.api['PLANNER'].get('BOM',bom['name']);self.log('forecast_evidence',role='PLANNER',order=doc,bom=recipe)
                    for component in recipe['items']:
                        code=component['item_code'];forecast[code]=forecast.get(code,0)+remaining*float(component['qty'])/float(recipe['quantity'])/7
        record=self.write('forecast:'+str(self.day),'ToDo',{'description':json.dumps({'as_of':self.date(),'daily_material_forecast':forecast,'basis':'Open submitted orders and active native BOMs; seven-day planning window'}),'allocated_to':'buyer@norvale.test'},'PLANNER')
        delivered=self.api['BUYER'].get('ToDo',record['name']);self.log('observation',role='BUYER',doctype='ToDo',as_of=self.date(),rows=[delivered])
        for code,value in json.loads(delivered['description'])['daily_material_forecast'].items():use[code]=max(use.get(code,0),value)
        return use

    def recurring_setup(self):
        self.configure();self.workflow_setup();self.finance_costs(True)
        from systems_v2 import identities
        aliases={'SC_MGR':'SCM_MGR','BUYER_CHEM':'BUYER','MAINT_LEAD':'MAINTENANCE','QA_TECH':'QA','ACCT_EXEC':'SALES','SHIFT_OP':'PRODUCTION','WH_CLERK':'WAREHOUSE'}
        roster=[{'role':aliases.get(r['role_id'],r['role_id']),'user':aliases.get(r['role_id'],r['role_id']).lower()+'@norvale.test','name':r['person'],'mail':r['email'].replace('.example','.test'),'manager':aliases.get(r.get('reports_to'),r.get('reports_to')) if r.get('reports_to')!='BOARD' else None} for r in ROLES if r['role_id']!='BOARD']
        roster.append({'role':'FINANCE','user':'finance@norvale.test','name':'Finance Operations','manager':'CFO'})
        self.state['identities']=identities(self,roster)
        self.write('mandate:2027','Note',{'title':'Operating mandates effective 2027-01-01','public':1,'content':json.dumps({'effective_date':'2027-01-01','review_date':'2027-05-01','SCM_MGR':'Approve or reject supplier purchases and item reorder changes using observed evidence; issue scoped purchasing policies.','CFO':'Review finances and revise purchasing policies; no configured spending ceiling.','BUYER':'Observe materials, request purchases; cannot approve own purchase or book stock.','IT_ADMIN':'Implement separately approved item changes after approval date.','PRODUCTION':'Execute authorized operations within available stock and shift capacity.'})})
        self.state.setdefault('ops',{'orders':{},'purchases':{},'production':{},'invoices':{},'maintenance':{},'changes':{},'meter':0,'serviced_meter':0,'shortage_days':{}})
        self.persist()

    def regime(self):
        from regimes import factors
        value=factors(self.config['seed'],self.day)
        self.log('private_regime',factors=value)
        return value

    def demand(self):
        def build():
            if datetime.fromisoformat(self.date()).weekday()>=5:return []
            rng=random.Random(self.config['seed']+self.day*7919)
            choices=rng.sample(self.catalog.skus,self.config['orders_per_day'])
            customers=list(self.catalog.accounts.values())
            return [{'key':f'd{self.day:04}-order-{s["sku_id"]}','item':s['sku_id'],'qty':max(12,round(s['daily_units']*self.config['demand_scale']*self.regime()['demand']*rng.uniform(.7,1.3)/12)*12),'rate':s['unit_price'],'customer':rng.choices(customers,[c['weight'] for c in customers])[0]['account_id'],'day':self.day} for s in choices]
        for p in self.plan(build):
            self.write(p['key'],'Sales Order',{'company':self.company,'customer':self.get('customer:'+p['customer'])['name'],'delivery_date':self.dated(self.day+7),'items':[{'item_code':p['item'],'qty':p['qty'],'rate':p['rate'],'delivery_date':self.dated(self.day+7),'warehouse':self.wh['fg']}]},'SALES',True)
            self.state['ops']['orders'].setdefault(p['key'],p);self.persist()

    def replenishment(self):
        ops=self.state['ops']
        def build():
            stock=self.inventory('BUYER');use=self.observed_usage();items=self.observe('BUYER','Item',{'item_code':['in',list(use)]},['name','item_code'])
            on_order={k:0 for k in use}
            for key,p in ops['purchases'].items():
                if p.get('received'):continue
                po=self.get(key,'BUYER')
                if not p.get('received') and po.get('workflow_state')!='Rejected':
                    for row in p['items']:on_order[row['item_code']]+=row['qty']
            groups={}
            for code,daily in use.items():
                if daily<=0 or code=='WATER':continue
                vendor=self.catalog.source_list[code];cost,(lead,sd),_=self.catalog.vendors[vendor]['supplies'][code]
                master=self.api['BUYER'].get('Item',code)
                configured=next((r['warehouse_reorder_level'] for r in master.get('reorder_levels',[]) if r['warehouse']==self.wh['rm']),0)
                targets=[p['reorder_target'] for p in self.effective_policies('SCM_MGR') if code in p['items']]
                threshold=max([daily*(lead+self.catalog.safety_days[code]),configured]+targets)
                current=stock.get((code,self.wh['rm']),0)+on_order[code]
                if current<threshold:
                    qty=max(12,math.ceil(max(daily*8,threshold-current)/12)*12)
                    groups.setdefault(vendor,[]).append({'item_code':code,'qty':qty,'rate':round(cost*self.regime()['cost'],4),'schedule_date':self.dated(self.day+lead),'warehouse':self.wh['hold']})
            return [{'key':f'd{self.day:04}-purchase-{vendor}','vendor':vendor,'items':rows,'day':self.day,'due':self.day+max(self.catalog.vendors[vendor]['supplies'][r['item_code']][1][0] for r in rows)} for vendor,rows in groups.items()]
        for p in self.plan(build):
            quotation=self.write('quote:'+p['key'],'Supplier Quotation',{'company':self.company,'supplier':self.get('vendor:'+p['vendor'])['name'],'transaction_date':self.date(),'valid_till':self.dated(self.day+7),'items':p['items']},'BUYER',True)
            quote_rows={r['item_code']:r for r in quotation['items']}
            po_items=[{**r,'supplier_quotation':quotation['name'],'supplier_quotation_item':quote_rows[r['item_code']]['name']} for r in p['items']]
            self.write(p['key'],'Purchase Order',{'company':self.company,'supplier':self.get('vendor:'+p['vendor'])['name'],'schedule_date':self.dated(p['due']),'items':po_items},'BUYER')
            ops['purchases'].setdefault(p['key'],p);self.persist()
            self.mail(p['key'],'BUYER','SCM_MGR','Replenishment approval requested',f"Native purchase {self.get(p['key'])['name']} replenishes released inventory below lead-time plus safety coverage.")

    def approvals(self):
        ops=self.state['ops']
        purchase_docs={key:self.get(key,'SCM_MGR') for key,p in ops['purchases'].items() if not p.get('received')}
        for key,p in list(ops['purchases'].items()):
            if p.get('received'):continue
            po=purchase_docs[key]
            if po['docstatus']==1:
                if 'arrival_due' not in p:
                    rng=random.Random(self.config['seed']+int(hashlib.sha256(key.encode()).hexdigest()[:8],16))
                    p['arrival_due']=max(self.day+1,p['due']+math.ceil((self.regime()['supply']-1)*max(1,p['due']-p['day']))+rng.choice([-1,0,0,2]));p['approved_day']=self.day;self.persist()
                continue
            if po.get('workflow_state')=='Rejected' or self.day<=p['day'] or self.day<p.get('review_after',0):continue
            first=not any(k.startswith('po-review:') for k in self.state['decisions'])
            usage=self.observed_usage()
            coverage=self.observe('SCM_MGR','Bin',{'item_code':['in',[r['item_code'] for r in po['items']]]},['item_code','warehouse','actual_qty'])
            evidence={k:po.get(k) for k in ['name','supplier','grand_total']}
            evidence['items']=[{k:r.get(k) for k in ['item_code','item_name','qty','uom','rate','amount','schedule_date']} for r in po['items']]
            quote=self.get(p.get('latest_quote','quote:'+key),'SCM_MGR')
            if quote.get('valid_till') and quote['valid_till']<self.date():
                latest_quote='requote:'+key+':'+str(self.day)
                quote=self.write(latest_quote,'Supplier Quotation',{'company':self.company,'supplier':po['supplier'],'transaction_date':self.date(),'valid_till':self.dated(self.day+7),'items':p['items']},'BUYER',True)
                p['latest_quote']=latest_quote;self.persist()
            evidence['supplier_receipts']=self.observe('SCM_MGR','Purchase Receipt',{'supplier':po['supplier'],'docstatus':1},['name','posting_date','supplier'])
            evidence['supplier_quote']={k:quote.get(k) for k in ['name','supplier','transaction_date','valid_till','grand_total']}
            evidence['supplier_quote']['items']=[{k:r.get(k) for k in ['item_code','qty','rate','amount']} for r in quote['items']]
            evidence['receiving_policy']='Purchased materials are received into Quarantine, inspected by QA, then transferred to Raw Materials. Zero optional historical price fields indicate no previous purchase, not a conflicting quote.'
            coverage=[{'item_code':row['item_code'],'released_raw_materials_qty':sum(b['actual_qty'] for b in coverage if b['item_code']==row['item_code'] and b['warehouse']==self.wh['rm']),'quarantine_qty':sum(b['actual_qty'] for b in coverage if b['item_code']==row['item_code'] and b['warehouse']==self.wh['hold']),'open_po_qty_excluding_this':sum(max(0,r['qty']-r.get('received_qty',0)) for other in purchase_docs.values() if other['name']!=po['name'] and other['docstatus'] in (0,1) and other.get('workflow_state')!='Rejected' for r in other['items'] if r['item_code']==row['item_code'])} for row in po['items']]
            evidence.update(coverage=coverage,observed_daily_usage={r['item_code']:usage.get(r['item_code'],0) for r in po['items']},other_open_orders=self.observe('SCM_MGR','Purchase Order',{'docstatus':1,'status':['not in',['Closed','Completed']]},['name','supplier','grand_total']))
            result=self.choose('po-review:'+key+':'+str(self.day),'SCM_MGR',evidence,'Decide this PO on its own current evidence: approve, defer, reject or investigate. Separately and optionally propose a scoped standing_policy effective tomorrow and reviewed in 14 days. Not having grounds for a policy is not a reason to defer this PO. The approval_limit applies per future PO; other_open_orders show observed supplier totals. Assess current observations rather than copying earlier reasons. Quarantine is the established receiving/inspection path, not a location conflict.',model=first)
            if result['action'] in ('approve','reject'):
                action='Approve' if result['action']=='approve' else 'Reject'
                doc=self.api['SCM_MGR'].call('frappe.model.workflow.apply_workflow',{'doc':json.dumps(po),'action':action});self.log('workflow_readback',role='SCM_MGR',doc=self.get(key,'SCM_MGR'))
                if action=='Approve':
                    rng=random.Random(self.config['seed']+int(hashlib.sha256(key.encode()).hexdigest()[:8],16))
                    p['arrival_due']=max(self.day+1,p['due']+math.ceil((self.regime()['supply']-1)*max(1,p['due']-p['day']))+rng.choice([-1,0,0,2]));p['approved_day']=self.day
            else:
                p['review_after']=self.day+2
                self.write('pending:'+key+':'+str(self.day),'ToDo',{'description':result['reason']+'; review '+self.dated(p['review_after']),'reference_type':'Purchase Order','reference_name':po['name'],'allocated_to':'buyer@norvale.test'},'SCM_MGR')
            self.persist()
        for key,c in list(ops['changes'].items()):
            if c.get('implemented'):continue
            if 'approved_day' not in c:
                if self.day<=c['requested_day'] or self.day<c.get('review_after',0):continue
                request=self.get(key,'SCM_MGR')
                result=self.choose('change-review:'+key+':'+str(self.day),'SCM_MGR',{'request':request,'stock':self.observe('SCM_MGR','Bin',{'item_code':c['item'],'warehouse':self.wh['rm']},['item_code','actual_qty'])},'Approve this evidence-grounded reorder-level change, or defer/reject/investigate.',model=True)
                if result['action']=='approve':
                    self.write('approval:'+key,'ToDo',{'reference_type':'ToDo','reference_name':request['name'],'description':json.dumps({'approved_item':c['item'],'reorder_level':c['level']}),'allocated_to':'it_admin@norvale.test'},'SCM_MGR')
                    c['approved_day']=self.day
                else:c['review_after']=self.day+7
            elif self.day>c['approved_day']:
                approval=self.get('approval:'+key,'IT_ADMIN');request=self.get(key,'IT_ADMIN')
                assert approval['owner']=='scm_mgr@norvale.test' and approval['reference_name']==request['name']
                payload=json.loads(approval['description']);assert payload=={'approved_item':c['item'],'reorder_level':c['level']}
                doc=self.api['IT_ADMIN'].update('Item',c['item'],{'reorder_levels':[{'warehouse':self.wh['rm'],'warehouse_reorder_level':c['level'],'warehouse_reorder_qty':c['level'],'material_request_type':'Purchase'}]})
                assert self.api['IT_ADMIN'].get('Item',c['item'])['reorder_levels'][0]['warehouse_reorder_level']==c['level']
                for d in [approval,request]:self.api['IT_ADMIN'].update('ToDo',d['name'],{'status':'Closed'})
                c['implemented']=self.day;self.log('master_implemented',doc=doc)
            self.persist()

    def arrivals(self):
        for key,p in self.state['ops']['purchases'].items():
            if p.get('received') or 'arrival_due' not in p or self.day<p['arrival_due']:continue
            if 'arrived_day' not in p:
                p['arrived_day']=self.day;self.state['physical'][key]={'arrived_day':self.day,'items':p['items'],'booked':False};self.persist()
                self.log('physical_arrival',purchase=key,items=p['items'])
            if self.day<p['arrived_day']+self.config['receipt_delay']:continue
            doc=self.mapped('WAREHOUSE','erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt',existing=('receipt:'+key,'Purchase Receipt'),source_name=self.get(key)['name'])
            for r in doc['items']:r['warehouse']=self.wh['hold']
            self.write('receipt:'+key,'Purchase Receipt',doc,'WAREHOUSE',True)
            p['received']=self.day;self.state['physical'][key]['booked']=True;self.persist()

    def inspect_release(self,key,item,qty,reference,raw=False):
        ref=self.get(reference,'QA');m=self.catalog.materials.get(item,{})
        attr=m.get('attr') if raw else None
        low,high=attr[2] if attr else (0,1)
        measured=attr[1] if attr else 0.5
        # Synthetic measured values, not inferred from approval or software status.
        q=self.write('qc:'+key,'Quality Inspection',{'inspection_type':'Incoming' if raw else 'In Process','reference_type':ref['doctype'],'reference_name':ref['name'],'item_code':item,'sample_size':1,'inspected_by':'qa@norvale.test','manual_inspection':1,'status':'Accepted','readings':[{'specification':attr[0] if attr else 'Visual release score (0-1)','reading_1':str(measured),'numeric':1,'min_value':low,'max_value':high}]},'QA',True)
        assert q['status']=='Accepted'
        self.write('release:'+key,'Stock Entry',{'company':self.company,'stock_entry_type':'Material Transfer','items':[{'item_code':item,'qty':qty,'s_warehouse':self.wh['hold'],'t_warehouse':self.wh['rm'] if raw else self.wh['fg']}]},'QA',True)

    def inspections(self):
        ops=self.state['ops']
        for key,p in ops['purchases'].items():
            if 'received' not in p or 'invoice:'+key in ops['invoices']:continue
            if not p.get('released'):
                for row in p['items']:self.inspect_release(key+':'+row['item_code'],row['item_code'],row['qty'],'receipt:'+key,True)
                p['released']=self.day;self.persist()
            doc=self.mapped('FINANCE','erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice',existing=('invoice:'+key,'Purchase Invoice'),source_name=self.get('receipt:'+key)['name'])
            doc['due_date']=self.dated(self.day+5)
            self.write('invoice:'+key,'Purchase Invoice',doc,'FINANCE',True)
            ops['invoices']['invoice:'+key]={'due':self.day+5};self.persist()
        for key,p in ops['production'].items():
            if p.get('released') or self.day<=p['day']:continue
            self.inspect_release(key,p['item'],p['qty'],'manufacture:'+key)
            p['released']=self.day
            self.state['physical'][key].update(awaits_inspection=False,quality_released_day=self.day)
            self.persist()

    def production(self):
        ops=self.state['ops']
        def build():
            if any(not m.get('closed') for m in ops['maintenance'].values()):return []
            stock=self.inventory('PLANNER');capacity=self.config['capacity_units'];out=[];short=[]
            for key,order in ops['orders'].items():
                if sum(p['qty'] for p in ops['production'].values() if p['order']==key)>=order['qty']:continue
                doc=self.get(key,'PLANNER');row=doc['items'][0]
                outstanding=float(row['qty'])-float(row.get('delivered_qty',0))
                committed=sum(p['qty'] for p in ops['production'].values() if p['order']==key)
                remaining=float(row['qty'])-committed
                if outstanding<=0 or remaining<=0 or capacity<12:continue
                bom=self.get('bom:'+order['item'],'PLANNER')['items']
                available=min(stock.get((r['item_code'],self.wh['rm']),0)/r['qty'] for r in bom)
                qty=int(min(remaining,capacity,available)//12)*12
                if qty<=0:
                    missing=[r['item_code'] for r in bom if stock.get((r['item_code'],self.wh['rm']),0)<r['qty']*min(remaining,12)]
                    short.extend(missing);continue
                p={'key':f'd{self.day:04}-work-'+key,'order':key,'item':order['item'],'qty':qty,'day':self.day}
                out.append(p);capacity-=qty
                for r in bom:stock[(r['item_code'],self.wh['rm'])]=stock.get((r['item_code'],self.wh['rm']),0)-r['qty']*qty
            return {'work':out,'shortages':sorted(set(short))}
        plan=self.plan(build)
        if plan==[]:self.log('blocked',reason='Maintenance owns unavailable equipment');return
        for item in plan['shortages']:
            ops['shortage_days'].setdefault(item,[])
            if self.day not in ops['shortage_days'][item]:ops['shortage_days'][item].append(self.day)
            key='shortage:'+item
            task=self.write(key,'ToDo',{'description':f'Planner observed missing released {item} for pending sales orders. Replenishment review required.','allocated_to':'scm_mgr@norvale.test'},'PLANNER')
            if len(ops['shortage_days'][item])>=2 and not any(c['item']==item for c in ops['changes'].values()):
                change='change:'+item;level=math.ceil(self.observed_usage().get(item,1)*12)
                self.write(change,'ToDo',{'description':f'Repeated native inventory shortage for {item}, observed days {ops["shortage_days"][item]}. Request reorder level {level}. Approval is not implementation.','allocated_to':'scm_mgr@norvale.test'},'BUYER')
                ops['changes'][change]={'item':item,'level':level,'requested_day':self.day}
                self.mail(change,'BUYER','SCM_MGR','Repeated shortage: master data request',self.get(change)['description'])
            self.persist()
        for p in plan['work']:
            from systems_v2 import operation_bom,job_cards,complete_job
            minutes_per_unit=self.config.get('shift_minutes',480)/self.config['capacity_units']
            routed_bom=operation_bom(self,p['item'],minutes_per_unit)
            wo=self.write(p['key'],'Work Order',{'production_item':p['item'],'bom_no':routed_bom['name'],'operations':[{'operation':r['operation'],'workstation':r['workstation'],'time_in_mins':p['qty']*minutes_per_unit,'batch_size':p['qty'],'bom':routed_bom['name'],'status':'Pending'} for r in routed_bom['operations']],'transfer_material_against':'Work Order','qty':p['qty'],'company':self.company,'use_multi_level_bom':0,'skip_transfer':1,'source_warehouse':self.wh['rm'],'wip_warehouse':self.wh['wip'],'fg_warehouse':self.wh['hold'],'planned_start_date':self.date()+' 08:00:00','sales_order':self.get(p['order'])['name']},'PLANNER',True)
            offset=sum(w['qty'] for w in plan['work'][:plan['work'].index(p)])
            start=datetime.fromisoformat(self.date()+' 08:00:00')+timedelta(minutes=offset*minutes_per_unit)
            end=start+timedelta(minutes=p['qty']*minutes_per_unit)
            for card in job_cards(self,wo['name']):
                actual=complete_job(self,card['name'],self.state['identities']['PRODUCTION']['employee'],start.isoformat(' '),end.isoformat(' '),p['qty'])
                self.log('readback',role='PRODUCTION',doc=actual)
                self.docs['job-card:'+p['key']]={'doctype':'Job Card','name':actual['name']};self.persist()
            doc=self.mapped('PRODUCTION','erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry',existing=('manufacture:'+p['key'],'Stock Entry'),work_order_id=wo['name'],purpose='Manufacture',qty=p['qty'])
            for r in doc['items']:
                if r.get('is_finished_item'):r['t_warehouse']=self.wh['hold']
                else:r['s_warehouse']=self.wh['rm']
            self.write('manufacture:'+p['key'],'Stock Entry',doc,'PRODUCTION',True)
            ops['production'].setdefault(p['key'],p)
            ops['meter']=sum(w['qty'] for w in ops['production'].values())
            self.state['physical'][p['key']]={'produced':p['qty'],'completed_day':self.day,'awaits_inspection':True};self.persist()
            self.log('physical_production',native_work_order=wo['name'],item=p['item'],qty=p['qty'],completed_day=self.day)

    def fulfillment(self):
        ops=self.state['ops']
        def build():
            stock=self.inventory('WAREHOUSE');out=[]
            open_names={r['name'] for r in self.observe('WAREHOUSE','Sales Order',{'docstatus':1,'per_delivered':['<',100]},['name'])}
            for key,p in ops['orders'].items():
                if self.docs[key]['name'] not in open_names:continue
                doc=self.get(key,'WAREHOUSE');row=doc['items'][0]
                qty=min(row['qty']-row.get('delivered_qty',0),stock.get((p['item'],self.wh['fg']),0))
                if qty<=0:continue
                out.append({'key':f'd{self.day:04}-delivery-'+key,'order':key,'qty':qty,'item':p['item']});stock[(p['item'],self.wh['fg'])]-=qty
            return out
        for p in self.plan(build):
            doc=self.mapped('WAREHOUSE','erpnext.selling.doctype.sales_order.sales_order.make_delivery_note',existing=(p['key'],'Delivery Note'),source_name=self.get(p['order'])['name'])
            doc['items'][0]['qty']=p['qty'];self.write(p['key'],'Delivery Note',doc,'WAREHOUSE',True)
            self.state['physical'][p['key']]={'departed_day':self.day,'qty':p['qty'],'item':p['item']};self.persist()
            self.log('physical_dispatch',native_delivery=self.docs[p['key']]['name'],item=p['item'],qty=p['qty'],departed_day=self.day)
            doc=self.mapped('FINANCE','erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice',existing=('invoice:'+p['key'],'Sales Invoice'),source_name=self.get(p['key'])['name'])
            doc['due_date']=self.dated(self.day+3)
            self.write('invoice:'+p['key'],'Sales Invoice',doc,'FINANCE',True)
            ops['invoices']['invoice:'+p['key']]={'due':self.day+3};self.persist()

    def settlements(self):
        for key,p in self.state['ops']['invoices'].items():
            if p.get('paid') or self.day<p['due']:continue
            self.pay('pay:'+key,key);p['paid']=self.day;self.persist()

    def service(self):
        from systems_v2 import Tickets
        tickets=Tickets(self.run)
        ops=self.state['ops']
        for key,m in ops['maintenance'].items():
            if m.get('closed') or self.day<m['due']:continue
            doc=self.get(key,'MAINTENANCE')
            if doc['status']!='Closed':self.api['MAINTENANCE'].update('ToDo',doc['name'],{'status':'Closed','description':doc['description']+' Technician inspection and restart test completed '+self.date()})
            ticket=tickets.close(m['ticket'], 'Technician inspection and restart test completed '+self.date()+'. ERP task '+doc['name'])
            if ticket['state']!='closed':raise RuntimeError('Ticket closure readback failed')
            self.log('ticket_readback',record=ticket)
            m['closed']=self.day;ops['serviced_meter']=m['meter'];self.log('maintenance_readback',doc=self.get(key,'MAINTENANCE'));self.persist()
        if ops['meter']-ops['serviced_meter']>=self.config['maintenance_units'] and not any(not m.get('closed') for m in ops['maintenance'].values()):
            key=f'service:{ops["meter"]}'
            self.write(key,'ToDo',{'description':f'Filler preventive service due after {ops["meter"]-ops["serviced_meter"]} produced units. Equipment unavailable until technician restart test.','allocated_to':'maintenance@norvale.test'},'MAINTENANCE')
            ticket=tickets.open(key,'Filler preventive service',self.get(key)['description']+' ERP task '+self.get(key)['name']+'; reported '+self.date())
            self.log('ticket_readback',record=ticket)
            ops['maintenance'][key]={'day':self.day,'due':self.day+self.config['maintenance_days'],'meter':ops['meter'],'ticket':ticket['number']};self.persist()
            self.mail(key,'MAINTENANCE','PLANT_MGR','Filler unavailable for service',self.get(key)['description'])

    def management(self):
        if (self.day+1)%self.config['report_every']:return
        key=f'report:{self.day}'
        period=self.config['report_every']/30
        self.write('costs:'+key,'Journal Entry',{'company':self.company,'posting_date':self.date(),'accounts':[{'account':'Salary - NCC','debit_in_account_currency':round(2500*period,2)},{'account':'Utility Expenses - NCC','debit_in_account_currency':round(300*period,2)},{'account':'Cash - NCC','credit_in_account_currency':round(2500*period,2)+round(300*period,2)}],'user_remark':'Prorated aggregate wages/utilities; prototype, not employee payroll'},'FINANCE',True)

        gl=self.observe('CFO','GL Entry',{'company':self.company,'posting_date':['<=',self.date()]},['account','debit','credit','voucher_type','voucher_no'])
        totals={}
        for r in gl:totals[r['account']]=round(totals.get(r['account'],0)+r['debit']-r['credit'],2)
        pending=self.observe('CFO','ToDo',{'status':'Open'},['name','description','allocated_to'])
        self.write(key,'Note',{'title':'Operating review '+self.date(),'public':0,'content':'<pre>'+json.dumps({'as_of':self.date(),'balances':totals,'outstanding_tasks':pending},indent=2)+'</pre>'},'CFO')
        urgent=any('urgent' in r.get('description','').lower() for r in pending)
        if not urgent and (self.day+1)//90==self.state.get('management_quarter',0):return
        result=self.choose('review:'+str(self.day),'CFO',{'report':self.get(key,'CFO')['name'],'native_balances':totals,'pending_requests':pending},'Review current financial records and pending requests. Approve the review or investigate/defer/reject with a concrete next request. Do not assume unseen production facts.',action='investigate',model=True)
        self.state['management_quarter']=(self.day+1)//90;self.persist()
        if result['action']!='approve':self.write('followup:'+key,'ToDo',{'description':result['reason'],'allocated_to':'finance@norvale.test','date':self.dated(self.day+3)},'CFO')
        self.mail(key,'CFO','CEO','Periodic operating review',result['message'])
    def recurring_events(self):
        yield 0,'seed',self.seed
        yield 0,'recurring-setup',self.recurring_setup
        for day in range(self.config['days']):
            for phase in ['service','arrivals','inspections','demand','fulfillment','production','replenishment','approvals','settlements','management']:
                yield day,f'd{day:04}:{phase}',getattr(self,phase)

    def through(self,day):
        """Safe Run All boundary: repeating a cell cannot pass its absolute day."""
        while True:
            pending=next(((d,k) for d,k,_ in self.events() if k not in self.state['completed']),None)
            if pending is None or pending[0]>day:return
            self.step()

    def extend(self,days,demand_scale=None):
        if days<self.config['days']:raise ValueError('Cannot shorten recorded horizon')
        if any(k not in self.state['completed'] for _,k,_ in self.events()):raise ValueError('Finish current horizon before changing its future demand')
        before=dict(self.config);self.config['days']=days
        if demand_scale is not None:self.config['demand_scale']=float(demand_scale)
        self.validate_config(self.config);save(self.run/'config.json',self.config)
        self.log('horizon_extended',previous=before,new=self.config,effective_day=before['days'])

    @staticmethod
    def validate_config(c):
        if not 1<=c['days']<=365:raise ValueError('days must be 1..365')
        if not 1<=c['orders_per_day']<=18:raise ValueError('orders_per_day must be 1..18')
        if not 0<c['demand_scale']<=1:raise ValueError('demand_scale must be in (0,1]')
        for k in ['capacity_units','maintenance_units','maintenance_days','report_every','receipt_delay']:
            if c[k]<=0:raise ValueError(k+' must be positive')
        if c['opening_stock_days']<0:raise ValueError('opening_stock_days must be nonnegative')

    def events(self):
        yield from (self.recurring_events() if self.config else self.fixture_events())

    def fixture_events(self):
        yield 0,'seed',self.seed
        yield 0,'purchasing-workflow',self.workflow_setup
        yield 0,'purchase-request',self.purchase
        yield 1,'purchase-approval',self.approve_purchase
        yield 2,'maintenance-request',self.maintenance_request
        yield 3,'physical-arrival',self.arrive
        yield 4,'native-permissions-and-retry-keys',self.configure
        yield 4,'receipt-booking',self.receipt
        yield 4,'incoming-quality',lambda:self.quality('incoming','SLES',500,'receipt') if 'receipt' in self.docs else None
        yield 4,'supplier-invoice',self.supplier_invoice
        yield 5,'maintenance-completion',self.maintenance_complete
        for i in range(3):
            yield 6,'sales-order:'+str(i),lambda i=i:self.order(i)
        for i in range(3):
            yield 7,'manufacture:'+str(i),lambda i=i:self.manufacture(i)
        for i in range(3):
            yield 8,'product-quality:'+str(i),lambda i=i:self.quality('product:'+str(i),self.catalog.skus[i]['sku_id'],240,'manufacture:'+str(i))
        yield 9,'master-change-request',self.change_request
        yield 10,'master-change-approval',self.approve_change
        for i in range(3):yield 11,'dispatch:'+str(i),lambda i=i:self.ship(i)
        yield 12,'master-change-implementation',lambda:self.implement_change() if 'change-approval' in self.docs else None
        for i in range(3):yield 12,'sales-invoice:'+str(i),lambda i=i:self.invoice(i)
        yield 14,'capital-funding',lambda:self.finance_costs(True)
        yield 15,'supplier-payment',lambda:self.pay('supplier-payment','supplier-invoice')
        for i in range(3):yield 20,'customer-payment:'+str(i),lambda i=i:self.pay('customer-payment:'+str(i),'invoice:'+str(i))
        yield 28,'monthly-wages-and-utilities',self.finance_costs
        yield 29,'monthly-review',self.report
        yield 30,'cfo-review',self.management_review

    def step(self, expected=None):
        with (self.run/'execution.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if (self.run/'state.json').exists():
                self.state=json.loads((self.run/'state.json').read_text());self.docs=self.state['docs'];self.day=self.state['day']
            if expected and expected in self.state['completed']:return {'event':expected,'already_completed':True}
            pending=next((k for _,k,_ in self.events() if k not in self.state['completed']),None)
            if expected and expected!=pending:raise ValueError(f'Next stage is {pending}, not {expected}')
            return self._step()

    def _step(self):
        for day,key,action in self.events():
            if key in self.state['completed']:continue
            self.day=day;self.event=key
            target=datetime(2027,1,1,12,tzinfo=timezone.utc)+timedelta(days=day)
            if self.stack.clock.now()<target:
                self.stack.settle();self.stack.clock.advance_to(target)
            start=time.monotonic()
            try:action()
            except Exception as exc:
                self.log('failed',error=str(exc));raise
            self.state['completed'].append(key);self.persist()
            result={'event':key,'day':day,'seconds':round(time.monotonic()-start,3),'native_documents':len(self.docs)}
            self.log('completed',**{'result':result});return result
        return None

    def run_all(self):
        import urllib.error
        attempts=0
        while True:
            try:event=self.step()
            except (urllib.error.URLError,TimeoutError,json.JSONDecodeError,ErpError) as exc:
                transient=not isinstance(exc,ErpError) or exc.status in (408,429,500,502,503,504)
                if not transient or attempts>=3:raise
                attempts+=1;self.log('stage_retry',attempt=attempts,error=str(exc));time.sleep(2**attempts);continue
            if event is None:return
            attempts=0;print(json.dumps(event),flush=True)

DEFAULT_CONFIG={'days':120,'seed':42,'demand_scale':0.02,'orders_per_day':3,'opening_stock_days':14,'capacity_units':600,'maintenance_units':3000,'shift_minutes':480,'maintenance_days':2,'report_every':7,'receipt_delay':1}

def initialize(path, config=None):
    path=Path(path).resolve()
    if path==Path(path.anchor):raise ValueError('Choose a dedicated run directory')
    if (path/'config.json').exists():
        existing=json.loads((path/'config.json').read_text())
        if config is not None and config!=existing:raise ValueError('Existing run config differs; choose a new directory or explicitly extend the finished horizon')
    elif (path/'state.json').exists() or (path/'env').exists():raise ValueError('Refusing to repurpose an existing non-recurring run')
    else:
        if path.exists() and any(path.iterdir()):raise ValueError('New run directory must be empty')
        config=dict(DEFAULT_CONFIG) if config is None else config
        Engine.validate_config(config);save(path/'config.json',config)
    if not (path/'env/site-created').exists():provision(path)
    return Engine(path)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--provision',action='store_true');p.add_argument('--run',type=Path,default=RUN.parent/'recurring-run');p.add_argument('--days',type=int);p.add_argument('--demand-scale',type=float);a=p.parse_args()
    if a.provision:
        config=dict(DEFAULT_CONFIG)
        if a.days is not None:config['days']=a.days
        if a.demand_scale is not None:config['demand_scale']=a.demand_scale
        initialize(a.run,config)
    else:
        e=Engine(a.run)
        if a.days is not None:e.extend(a.days,a.demand_scale)
        elif a.demand_scale is not None:p.error('--demand-scale on an existing run requires --days to extend its future')
        e.run_all()
