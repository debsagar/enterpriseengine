"""Offline behavior test: mocked JEV/LLM, no services or paid calls."""
from copy import deepcopy
from company import Engine
import router
p={'role':'SCM_MGR','effective_date':'2027-01-02','review_date':'2027-01-20','approval_limit':100,'reorder_target':10,'suppliers':['S'],'items':['M']}
evidence={'supplier':'S','grand_total':50,'items':[{'item_code':'M','qty':5,'rate':10}],'coverage':[{'item_code':'M','released_raw_materials_qty':2,'quarantine_qty':0,'open_po_qty_excluding_this':0}]}
e=Engine.__new__(Engine);e.day=3;e.state={'standing_policies':[p],'decisions':{}}
e.persist=lambda:None;e.log=lambda *a,**kw:None
for day in ['2027-01-01','2027-01-03']:
 e.state['decisions'][day]={'mode':'model','input':{'role':'SCM_MGR','as_of':day,'evidence':evidence},'output':{'action':'defer','reason':'Historical request'}}
calls=[]
def route(ev,cases,policies,on_attempt=None):
 calls.append((ev,cases,policies));return {'judgment':not policies,'probability':.1 if policies else .9}
router.route=route
judgments=[]
def decision(key,role,ev,question):
 judgments.append(key);return {'output':{'action':'investigate'}}
e.decision=decision
assert e.choose('known','SCM_MGR',evidence,'Review')['action']=='approve'
assert e.state['decisions']['known']['mode']=='standing-policy'
assert calls[0][0]['policy_check']['within_dates'] is True
assert [c['as_of'] for c in calls[0][1]]==['2027-01-03']
assert e.choose('repeat','SCM_MGR',evidence,'Review')['action']=='approve' and len(calls)==1
changed=deepcopy(evidence);changed['coverage'][0]['open_po_qty_excluding_this']=10
e.choose('changed','SCM_MGR',changed,'Review');assert len(calls)==2
outside={**evidence,'grand_total':101}
assert e.choose('outside','SCM_MGR',outside,'Review')['action']=='investigate' and len(calls)==3
assert judgments==['outside']
e.day=25
e.choose('expired','SCM_MGR',evidence,'Review');assert judgments[-1]=='expired' and len(calls)==4
print('PASS: policy authorization, JEV false -> rules, dated memory, unchanged-case reuse, changed-coverage recheck, nonmatch and expiry -> JEV -> judgment')
