"""Regression: recover mapped records before ERP rejects a consumed source."""
from environments.norvale.native.company import Engine
class API:
 def get(self,dt,name):return {'doctype':dt,'name':name,'docstatus':1}
 def list(self,dt,filters):return [{'name':'BOOKED-1'}]
 def call(self,*args,**kwargs):raise AssertionError('Consumed source must not be mapped again')
e=Engine.__new__(Engine);e.docs={'manufacture:x':{'doctype':'Stock Entry','name':'BOOKED-1'}};e.api={'PRODUCTION':API()};e.persist=lambda:None;e.log=lambda *a,**kw:None
for lose_mapping in [False,True]:
 if lose_mapping:e.docs.clear()
 d=e.mapped('PRODUCTION','make_stock_entry',existing=('manufacture:x','Stock Entry'),work_order_id='COMPLETED-WO')
 assert d['name']=='BOOKED-1' and d['docstatus']==1 and e.docs['manufacture:x']['name']=='BOOKED-1'
print('PASS: consumed source is not remapped, with retained or recovered native identity')
