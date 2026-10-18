// Disposable integration fixture. Never deployed with the application.
import http from 'node:http';
import {randomBytes, createHmac} from 'node:crypto';
const orders = new Map(), payments = new Map(), refunds = new Map(), refundKeys = new Map();
const secret = 'fixture-api-secret', webhook = 'fixture-webhook-secret';
const id = prefix => prefix + randomBytes(8).toString('hex');
const hmac = (key, value) => createHmac('sha256', key).update(value).digest('hex');
const server = http.createServer(async (req, res) => {
  const reply = (status, value) => { res.writeHead(status, {'Content-Type': 'application/json'}); res.end(JSON.stringify(value)); };
  let raw = ''; for await (const chunk of req) { raw += chunk; if (raw.length > 262144) return reply(413, {}); }
  let body = {}; try { if (raw) body = JSON.parse(raw); } catch { return reply(400, {}); }
  const path = new URL(req.url, 'http://fixture').pathname;
  if (path === '/health') return reply(200, {status: 'ok'});
  if (path === '/_orders') return reply(200, [...orders.values()]);
  if (path.startsWith('/_capture/')) {
    const order = orders.get(path.split('/').at(-1)); if (!order) return reply(404, {});
    let payment = [...payments.values()].find(value => value.order_id === order.id);
    if (!payment) { payment = {id: id('pay_'), order_id: order.id, amount: order.amount, currency: order.currency, captured: true, status: 'captured'}; payments.set(payment.id, payment); }
    order.status = 'paid';
    return reply(200, {razorpay_order_id: order.id, razorpay_payment_id: payment.id, razorpay_signature: hmac(secret, order.id + '|' + payment.id)});
  }
  if (path.startsWith('/_webhook/')) {
    const payment = payments.get(path.split('/').at(-1)); if (!payment) return reply(404, {});
    const payload = JSON.stringify({event: 'payment.captured', payload: {payment: {entity: payment}}});
    return reply(200, {raw: payload, signature: hmac(webhook, payload), eventId: id('evt_')});
  }
  if (req.headers.authorization !== 'Basic ' + Buffer.from('rzp_test_fixture:' + secret).toString('base64')) return reply(401, {});
  if (req.method === 'POST' && path === '/v1/orders') {
    const order = {...body, id: id('order_'), status: 'created'}; orders.set(order.id, order); return reply(200, order);
  }
  if (req.method === 'GET' && path.startsWith('/v1/orders/')) return reply(orders.has(path.split('/').at(-1)) ? 200 : 404, orders.get(path.split('/').at(-1)) || {});
  if (req.method === 'GET' && path.startsWith('/v1/payments/')) return reply(payments.has(path.split('/').at(-1)) ? 200 : 404, payments.get(path.split('/').at(-1)) || {});
  if (req.method === 'POST' && /^\/v1\/payments\/pay_[a-z0-9]+\/refund$/.test(path)) {
    const key = req.headers['x-refund-idempotency']; if (!key) return reply(400, {});
    if (refundKeys.has(key)) return reply(200, refunds.get(refundKeys.get(key)));
    const payment = payments.get(path.split('/')[3]); if (!payment || body.amount !== payment.amount) return reply(400, {});
    const refund = {id: id('rfnd_'), payment_id: payment.id, amount: body.amount, status: 'processed'};
    refunds.set(refund.id, refund); refundKeys.set(key, refund.id); return reply(200, refund);
  }
  if (req.method === 'GET' && path.startsWith('/v1/refunds/')) return reply(refunds.has(path.split('/').at(-1)) ? 200 : 404, refunds.get(path.split('/').at(-1)) || {});
  return reply(404, {});
});
server.listen(19090, '0.0.0.0');
