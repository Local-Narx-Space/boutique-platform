"""Real HTTP smoke checks. Successful structured evidence is written only after all checks pass."""
import argparse
import concurrent.futures
import datetime
import http.cookiejar
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

CLIENT_HEADERS = {'User-Agent': 'BoutiqueSmoke/1.0', 'Accept': 'application/json'}

CHECKS = ['browse','cart','validation','origin','session_isolation','server_pricing','checkout','repeat_idempotency','concurrent_idempotency','order_ownership','empty_cart','deletion']

def require(condition, description):
    if not condition:
        raise RuntimeError('Smoke check failed: '+description)

def run(base):
    def browser():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    a,b=browser(),browser()
    def req(client,path,method='GET',body=None,key=None,origin=True):
        headers=dict(CLIENT_HEADERS)
        if origin: headers['Origin']=base
        if key: headers['Idempotency-Key']=key
        if body is not None: headers['Content-Type']='application/json'
        request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else (b'' if method=='POST' else None),headers=headers,method=method)
        try:
            with client.open(request,timeout=10) as response: return response.status,json.load(response)
        except urllib.error.HTTPError as response: return response.code,json.load(response)
    for _ in range(60):
        try:
            if req(a,'/health/ready')[0]==200: break
        except (urllib.error.URLError,ConnectionError,TimeoutError,ValueError): pass
        time.sleep(2)
    else: raise RuntimeError('Readiness failed')
    require(len(req(a,'/api/products')[1])==4,'browse')
    require(req(a,'/api/cart')[1]['items']==[],'empty initial cart')
    require(req(a,'/api/cart/items/mug','PUT',{'quantity':2})[0]==200,'cart update')
    require(req(a,'/api/cart/items/mug','PUT',{'quantity':21})[0]==400,'quantity limit')
    require(req(a,'/api/cart/items/mug','PUT',{'quantity':1},origin=False)[0]==403,'origin enforcement')
    require(req(a,'/api/cart/items/missing','PUT',{'quantity':1})[0]==404,'missing product')
    require(req(b,'/api/cart')[1]['items']==[],'session isolation')
    status,order=req(a,'/api/orders','POST',key='smoke-checkout-001')
    require(status==200,'checkout')
    require(order['total']==3600 and order['currency']=='USD','server pricing')
    require(req(a,'/api/orders','POST',key='smoke-checkout-001')[1]['id']==order['id'],'repeat idempotency')
    require(req(a,'/api/orders/'+order['id'])[1]['total']==3600,'order retrieval')
    require(req(b,'/api/orders/'+order['id'])[0]==404,'order ownership')
    require(req(b,'/api/orders','POST',key='smoke-empty-001')[0]==409,'empty checkout')
    cookie='; '.join(f'{item.name}={item.value}' for handler in a.handlers if hasattr(handler,'cookiejar') for item in handler.cookiejar)
    def retry(_):
        request=urllib.request.Request(base+'/api/orders',data=b'',headers={**CLIENT_HEADERS,'Origin':base,'Cookie':cookie,'Idempotency-Key':'smoke-concurrent-001'},method='POST')
        with urllib.request.urlopen(request,timeout=15) as response: return json.load(response)['id']
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        require(len(set(pool.map(retry,range(4))))==1,'concurrent idempotency')
    require(req(a,'/api/cart/items/mug','DELETE')[1]['items']==[],'deletion')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',choices=['dev','staging','production'],default=os.environ.get('TARGET','dev'))
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    base=os.environ.get('BASE_URL','http://localhost:8080').rstrip('/')
    origin=urllib.parse.urlsplit(base)
    if origin.scheme not in ('http','https') or not origin.hostname or origin.username or origin.password or origin.path or origin.query or origin.fragment:
        parser.error('BASE_URL must be an application origin')
    report={'schema_version':1,'environment':args.environment,'origin':base,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'success':False,'checks':[]}
    try:
        run(base)
        report.update(success=True,checks=CHECKS)
        print('PASS: '+', '.join(CHECKS))
    finally:
        report['completed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__': main()
