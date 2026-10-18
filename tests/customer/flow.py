#!/usr/bin/env python3
"""Real HTTP commerce checks against ONLY the disposable local customer fixture."""
import argparse
import concurrent.futures
import http.cookiejar
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import uuid

BASE = 'http://127.0.0.1:18088'
FIXTURE = 'http://127.0.0.1:19090'
DELIVERY = {'name':'Sample Customer','address':'1 Demo Lane','city':'Test City','postalCode':'123456','country':'India'}
PASSWORD = 'unique disposable test passphrase'


class Browser:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, path, method='GET', body=None, headers=None):
        request = urllib.request.Request(BASE + path, method=method, headers={
            'Origin':BASE, 'Content-Type':'application/json', **(headers or {}),
        }, data=json.dumps(body).encode() if body is not None else b'' if method=='POST' else None)
        try:
            with self.client.open(request, timeout=30) as response: return response.status,json.load(response)
        except urllib.error.HTTPError as response: return response.code,json.load(response)

    def expect(self, status, *args, **kwargs):
        actual, body = self.request(*args, **kwargs)
        if actual != status: raise AssertionError(f'{args[0]}: expected {status}, got {actual}: {body}')
        return body


def fixture(path):
    with urllib.request.urlopen(FIXTURE + path, timeout=10) as response: return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-container')
    parser.add_argument('--report', type=Path, default=Path('/tmp/boutique-customer-flow.json'))
    args = parser.parse_args()
    engine = ['docker']
    if args.engine_container:
        engine += ['exec','-i','-e','DOCKER_CONFIG=/tmp/boutique-customer-docker',args.engine_container,'docker']
    # Hardcoded local origins and a fixture-only database container prevent targeting a real environment.
    def sql(statement):
        return subprocess.check_output(engine+['exec','-i','boutique-customer-pg','psql','-U','postgres','-d','boutique','-v','ON_ERROR_STOP=1','-t','-A'], input=statement.encode()).decode().strip()

    completed = []
    def passed(name): completed.append(name); print('PASS ' + name, flush=True)
    report = {'fixture':True,'origin':BASE,'success':False,'checks':completed}
    try:
        admin, a, b, guest = Browser(), Browser(), Browser(), Browser()
        suffix = uuid.uuid4().hex[:12]
        admin_email = 'admin-' + suffix + '@example.test'
        email = 'customer-' + suffix + '@example.test'
        other_email = 'other-' + suffix + '@example.test'
        admin.expect(200,'/api/auth/register','POST',{'email':admin_email,'password':PASSWORD,'name':'Fixture Admin','role':'admin'})
        admin.expect(403,'/api/admin/dashboard')
        sql("UPDATE customers SET role='admin' WHERE email='" + admin_email + "';")
        assert admin.expect(200,'/api/auth/session')['user']['role']=='admin'
        admin.expect(200,'/api/admin/dashboard')
        passed('admin bootstrap is explicit; registration cannot choose admin role')

        products = guest.expect(200,'/api/products')
        assert products and all(product['currency']=='INR' for product in products)
        product = next(product for product in products if product['id']=='mug')
        a.expect(200,'/api/cart')
        a.expect(200,'/api/cart/items/mug','PUT',{'quantity':2})
        identity = a.expect(200,'/api/auth/register','POST',{'email':email.upper(),'password':PASSWORD,'name':'Sample Customer'})
        assert identity['user']['email']==email and 'token' not in identity and identity['user']['role']=='customer'
        assert a.expect(200,'/api/cart')['items']==[{'productId':'mug','quantity':2}]
        a.expect(200,'/api/cart')  # Repeating merge must not double quantities.
        signed_in_elsewhere = Browser()
        signed_in_elsewhere.expect(200,'/api/auth/login','POST',{'email':email,'password':PASSWORD})
        assert signed_in_elsewhere.expect(200,'/api/cart')['items']==[{'productId':'mug','quantity':2}]
        b.expect(200,'/api/auth/register','POST',{'email':other_email,'password':PASSWORD,'name':'Other Customer'})
        assert b.expect(200,'/api/cart')['items']==[]
        a.expect(403,'/api/admin/products')
        assert sql("SELECT password_hash FROM customers WHERE email='" + email + "';").startswith('pbkdf2-sha256$600000$')
        passed('accounts persist; guest cart merges atomically; customer cart survives another browser')

        key = 'fixture-checkout-' + suffix
        order = a.expect(200,'/api/orders','POST',{**DELIVERY,'total':1},headers={'Idempotency-Key':key})
        assert order['total']==2*product['price'] and order['currency']=='INR'
        assert order['status']=='payment_pending' and order['payment_status']=='unpaid'
        assert a.expect(200,'/api/orders','POST',DELIVERY,headers={'Idempotency-Key':key})['id']==order['id']
        b.expect(404,'/api/orders/'+order['id'])
        guest.expect(404,'/api/orders/'+order['id'])
        assert order['id'] in [item['id'] for item in signed_in_elsewhere.expect(200,'/api/orders')['orders']]
        assert b.expect(200,'/api/orders')['orders']==[]
        passed('server pricing, account-owned history, access isolation and idempotent order creation')

        initiated = a.expect(200,'/api/orders/'+order['id']+'/payment','POST')
        assert initiated['amount']==order['total'] and initiated['mode']=='test'
        again = a.expect(200,'/api/orders/'+order['id']+'/payment','POST')
        assert again['providerOrderId']==initiated['providerOrderId']
        b.expect(404,'/api/orders/'+order['id']+'/payment','POST')
        proof = fixture('/_capture/'+initiated['providerOrderId'])
        a.expect(400,'/api/orders/'+order['id']+'/payment/verify','POST',{**proof,'razorpay_signature':'0'*64})
        assert a.expect(200,'/api/orders/'+order['id'])['payment_status']=='awaiting_payment'
        a.expect(200,'/api/orders/'+order['id']+'/payment/verify','POST',proof)
        a.expect(200,'/api/orders/'+order['id']+'/payment/verify','POST',proof)
        assert a.expect(200,'/api/orders/'+order['id'])['payment_status']=='paid'
        passed('payment initialization reuses provider order; forged success is rejected; captured payment is verified')

        event = fixture('/_webhook/'+proof['razorpay_payment_id'])
        def webhook(signature):
            request = urllib.request.Request(BASE+'/api/payments/webhook',data=event['raw'].encode(),method='POST',
                headers={'Content-Type':'application/json','X-Razorpay-Signature':signature,'X-Razorpay-Event-Id':event['eventId']})
            try:
                with urllib.request.urlopen(request,timeout=15) as response: return response.status,json.load(response)
            except urllib.error.HTTPError as response: return response.code,json.load(response)
        assert webhook('0'*64)[0]==400
        assert webhook(event['signature'])[0]==200
        assert webhook(event['signature'])[1]['status']=='already_received'
        passed('raw signed webhook works without browser Origin; forged and repeated events are safe')

        a.expect(403,'/api/admin/orders/'+order['id']+'/refund','POST',{'reason':'Not an administrator'})
        before = next(item for item in admin.expect(200,'/api/admin/products') if item['id']=='mug')['stock']
        admin.expect(200,'/api/admin/orders/'+order['id']+'/refund','POST',{'reason':'Fixture full refund'})
        admin.expect(200,'/api/admin/orders/'+order['id']+'/refund','POST',{'reason':'Repeated request'})
        assert a.expect(200,'/api/orders/'+order['id'])['status']=='refunded'
        after = next(item for item in admin.expect(200,'/api/admin/products') if item['id']=='mug')['stock']
        assert after==before+2
        passed('admin-only full refunds are idempotent and restock unshipped items once')

        sku = 'fixture-' + suffix
        saved = admin.expect(200,'/api/admin/products/'+sku,'PUT',{'id':sku,'name':'Fixture item','description':'Local fixture only','emoji':'◈','price':109900,'stock':3,'active':True,'version':0})
        assert any(item['id']==sku for item in guest.expect(200,'/api/products'))
        a.expect(200,'/api/cart/items/mug','DELETE')
        a.expect(200,'/api/cart/items/'+sku,'PUT',{'quantity':3})
        reserved = a.expect(200,'/api/orders','POST',DELIVERY,headers={'Idempotency-Key':'stock-'+suffix})
        b.expect(200,'/api/cart/items/'+sku,'PUT',{'quantity':1})
        b.expect(409,'/api/orders','POST',DELIVERY,headers={'Idempotency-Key':'no-stock-'+suffix})
        admin.expect(409,'/api/admin/products/'+sku,'PUT',{**saved,'stock':999})
        admin.expect(200,'/api/admin/orders/'+reserved['id']+'/status','POST',{'status':'cancelled'})
        updated = next(item for item in admin.expect(200,'/api/admin/products') if item['id']==sku)
        assert updated['stock']==3
        admin.expect(200,'/api/admin/products/'+sku,'PUT',{**updated,'active':False})
        assert not any(item['id']==sku for item in guest.expect(200,'/api/products'))
        passed('dynamic product management, stock reservations, conflict detection, cancellation and archiving')

        a.expect(200,'/api/cart/items/'+sku,'DELETE')
        a.expect(200,'/api/cart/items/mug','PUT',{'quantity':1})
        concurrent_key = 'concurrent-' + suffix
        cookies = '; '.join(cookie.name+'='+cookie.value for cookie in a.jar)
        def place(_):
            browser = Browser()
            return browser.expect(200,'/api/orders','POST',DELIVERY,headers={'Cookie':cookies,'Idempotency-Key':concurrent_key})
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            concurrent_orders = list(pool.map(place,range(3)))
        assert len({item['id'] for item in concurrent_orders})==1
        second = concurrent_orders[0]
        begin = a.expect(200,'/api/orders/'+second['id']+'/payment','POST')
        proof = fixture('/_capture/'+begin['providerOrderId'])
        a.expect(200,'/api/orders/'+second['id']+'/payment/verify','POST',proof)
        admin.expect(409,'/api/admin/orders/'+second['id']+'/status','POST',{'status':'shipped'})
        for status in ('processing','shipped','delivered'):
            admin.expect(200,'/api/admin/orders/'+second['id']+'/status','POST',{'status':status})
        before = next(item for item in admin.expect(200,'/api/admin/products') if item['id']=='mug')['stock']
        admin.expect(200,'/api/admin/orders/'+second['id']+'/refund','POST',{'reason':'Delivered fixture refund'})
        after = next(item for item in admin.expect(200,'/api/admin/products') if item['id']=='mug')['stock']
        assert after==before  # A financial refund does not prove delivered goods were returned.
        assert admin.expect(200,'/api/admin/audit')
        dashboard = admin.expect(200,'/api/admin/dashboard')
        sales = next(item for item in dashboard['sales'] if item['currency']=='INR')
        assert int(sales['gross']) >= order['total']+second['total'] and int(sales['refunds'])>=order['total']+second['total']
        passed('concurrent checkout, validated fulfilment transitions, delivered refunds and real sales/audit records')

        old_cookie = '; '.join(cookie.name+'='+cookie.value for cookie in a.jar)
        a.expect(200,'/api/auth/logout','POST')
        revoked = Browser()
        assert revoked.expect(200,'/api/auth/session',headers={'Cookie':old_cookie})['user'] is None
        revoked.expect(401,'/api/orders',headers={'Cookie':old_cookie})
        passed('logout revokes the server session; replayed cookies cannot retrieve account orders')
        report['success']=True
    finally:
        args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    main()
