# Service-level objectives

These are initial operating objectives for the Boutique production environment, owned by the application/platform operator (the repository owner in this lab). They are not customer SLAs or measured achievements. Review them after a representative load baseline, monthly and after material incidents. Dev/staging exercise the same checks but have no availability commitment.

## Objectives and measurement

| Objective | Good event / eligible events | Target | Window |
| --- | --- | --- | --- |
| API response availability | Completed eligible storefront API requests returning 2xx/3xx, divided by completed requests returning 2xx/3xx/5xx | 99.9% | rolling 30 days |
| API latency | Successful eligible API requests completing within 1 second, divided by all successful eligible API requests | 99.0% | rolling 30 days |
| Checkout response availability | Successful `POST /api/orders` responses, divided by eligible checkout responses (2xx/3xx/5xx) | 99.9% | rolling 30 days |

Measure at the frontend so a user request traversing several microservices is counted once. Include GET products/cart/order, PUT/DELETE cart items and POST orders. Use the existing bounded `route` and `method` labels. Exclude health checks, `/metrics`, static assets, unknown routes and OPTIONS/HEAD. Expected 4xx validation/ownership/empty-cart responses are excluded; review spikes separately because a regression can incorrectly reject valid clients. Planned maintenance affecting users consumes the budget.

The existing frontend metrics are `boutique_http_requests_total`, `boutique_http_request_duration_seconds_bucket` and `boutique_http_request_duration_seconds_count`. The histogram has an actual `le="1"` bucket. Select the intended `environment` and `cluster`; never combine dev traffic with production or sum the frontend and backend request counters.

### Measurement readiness

The shared-cluster homelab Helm profile now configures **35 days** time retention
with a **4GB block cap** on the existing 5Gi PVC. Other profiles and Compose retain
their seven-day default. The size cap may evict data before 35 days, and WAL/head
storage needs additional space. A `[30d]` query silently uses partial history when
that is all that exists. Report observed coverage, and mark the 30-day objective
**not evaluable** until continuous collection and sufficient storage support the
full window. Do not treat a newly populated dashboard as a 30-day achievement.

These response-based SLIs cannot see DNS, TLS, load-balancer failures or requests that never reach/finish at the frontend. A scrape `up` metric is not user availability. The shared profile now probes public HTTPS readiness through Cloudflare with certificate verification, but that prober runs in the same cluster. An independent external HTTPS journey check is still needed to detect a total host/cluster outage and establish complete user-facing availability. Long-term journey reports are not implemented. The functional smoke suite already exercises browsing, cart and checkout, but running it during deployment does not provide continuous availability coverage. It creates disposable orders, so continuous synthetic checkout needs an agreed test-data policy.

An empty denominator, insufficient history or missing telemetry is **unknown**, never 100% availability. Prometheus process/pod health and incident receiver delivery need separate operational checks. The shared profile includes production-only availability/latency multi-window burn alerts and a production SLO dashboard. Critical alerts require 14.4x burn in both 5-minute and 1-hour windows; warning alerts require 6x in both 30-minute and 6-hour windows. The corresponding long window requires at least 100 eligible responses. Low volume stays unknown rather than becoming a success claim; missing-target and public readiness alerts remain independent. Checkout has a documented separate objective, but no dedicated checkout burn alert yet.

### PromQL examples

These queries require the complete history above. For a labelled provisional lab view, replace `[30d]` with `[7d]` and select your actual cluster/environment. Run them per route as well when investigating aggregate results; high browse traffic can conceal checkout failures.

API response availability (ratio from 0 to 1):

```promql
1 - (
  (
    sum(increase(boutique_http_requests_total{service="frontend",environment="production",cluster="production",route=~"/api/(products|cart|cart/items/:product|orders|orders/:id)",method=~"GET|POST|PUT|DELETE",status=~"5.."}[30d]))
    or
    0 * sum(increase(boutique_http_requests_total{service="frontend",environment="production",cluster="production",route=~"/api/(products|cart|cart/items/:product|orders|orders/:id)",method=~"GET|POST|PUT|DELETE",status=~"[235].."}[30d]))
  )
  /
  sum(increase(boutique_http_requests_total{service="frontend",environment="production",cluster="production",route=~"/api/(products|cart|cart/items/:product|orders|orders/:id)",method=~"GET|POST|PUT|DELETE",status=~"[235].."}[30d]))
)
```

API latency good-event ratio (the numerator and denominator use the same successful response population):

```promql
sum(increase(boutique_http_request_duration_seconds_bucket{service="frontend",environment="production",cluster="production",route=~"/api/(products|cart|cart/items/:product|orders|orders/:id)",method=~"GET|POST|PUT|DELETE",status=~"[23]..",le="1"}[30d]))
/
sum(increase(boutique_http_request_duration_seconds_count{service="frontend",environment="production",cluster="production",route=~"/api/(products|cart|cart/items/:product|orders|orders/:id)",method=~"GET|POST|PUT|DELETE",status=~"[23].."}[30d]))
```

Checkout response availability:

```promql
1 - (
  (
    sum(increase(boutique_http_requests_total{service="frontend",environment="production",cluster="production",route="/api/orders",method="POST",status=~"5.."}[30d]))
    or
    0 * sum(increase(boutique_http_requests_total{service="frontend",environment="production",cluster="production",route="/api/orders",method="POST",status=~"[235].."}[30d]))
  )
  /
  sum(increase(boutique_http_requests_total{service="frontend",environment="production",cluster="production",route="/api/orders",method="POST",status=~"[235].."}[30d]))
)
```

The zero-error fallback handles a counter series that has never emitted a 5xx; it does not turn zero traffic into success. A 0/0 result remains unknown.

## Error-budget policy

For availability and checkout, the permitted bad-event fraction is 0.001; latency permits 0.01. Allowed bad events = eligible events × permitted fraction. Budget consumption = observed bad fraction / permitted fraction; burn rate uses the same ratio over a shorter window. For example, one million eligible API responses allow 1,000 5xx responses. This is a request budget, not a guaranteed number of outage minutes.

Use a weekly review while learning. Record the window, complete/partial coverage, eligible/good/bad counts, ratio, budget consumption, related incidents and owner. Low traffic and missing probes limit the conclusions; do not extrapolate a deployment smoke pass into a monthly SLO result.

If a complete window exhausts a budget, pause discretionary production changes, prioritize reliability fixes and recovery work, and document the owner's decision before resuming. Incident remediation and security fixes remain possible after review. This is an operator policy, not a new automatic Jenkins gate. Protected GitOps production review and preceding-environment rollout/smoke checks continue to apply.

Server-side pricing, session ownership and checkout idempotency are release acceptance invariants exercised by the smoke suite. Violating those invariants blocks release or triggers incident response even when HTTP success ratios look healthy. Recovery objectives and accepted data-loss limits are in [recovery-targets.md](recovery-targets.md).
