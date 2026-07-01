"""Live demo dashboard for the reliability gateway (stdlib only, no extra deps).

Run:
    python scripts/demo_server.py
    # then open http://localhost:8000

What it shows in real time:
  - Send a prompt through the gateway and watch which route answers it
    (cache_hit / primary / fallback / static_fallback), with latency & cost.
  - Circuit-breaker state per provider (CLOSED / OPEN / HALF_OPEN) + counters.
  - Chaos injection: change each provider's live failure rate with a slider and
    watch the breakers trip and traffic fail over.
  - Rolling metrics: availability, cache-hit rate, P95 latency, cost saved.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from reliability_lab.chaos import build_gateway, calculate_recovery_time_ms, load_queries
from reliability_lab.config import load_config
from reliability_lab.metrics import RunMetrics

CONFIG_PATH = "configs/default.yaml"

config = load_config(CONFIG_PATH)
QUERIES = load_queries()
# Mutable state container so /api/reset can swap gateway/metrics without
# tangling with `global` declarations inside request handlers.
STATE: dict[str, object] = {"gateway": build_gateway(config), "metrics": RunMetrics()}


def _record(result) -> None:  # type: ignore[no-untyped-def]
    metrics: RunMetrics = STATE["metrics"]  # type: ignore[assignment]
    metrics.total_requests += 1
    metrics.estimated_cost += result.estimated_cost
    if result.cache_hit:
        metrics.cache_hits += 1
        metrics.estimated_cost_saved += 0.001
        metrics.successful_requests += 1
    elif result.route == "fallback":
        metrics.fallback_successes += 1
        metrics.successful_requests += 1
    elif result.route == "static_fallback":
        metrics.static_fallbacks += 1
        metrics.failed_requests += 1
    else:
        metrics.successful_requests += 1
    if result.latency_ms > 0:
        metrics.latencies_ms.append(result.latency_ms)


def _state() -> dict[str, object]:
    gateway = STATE["gateway"]
    metrics: RunMetrics = STATE["metrics"]  # type: ignore[assignment]
    breakers = []
    for name, b in gateway.breakers.items():
        provider = next(p for p in gateway.providers if p.name == name)
        breakers.append(
            {
                "name": name,
                "state": b.state.value,
                "failure_count": b.failure_count,
                "success_count": b.success_count,
                "open_count": sum(1 for t in b.transition_log if t["to"] == "open"),
                "fail_rate": round(provider.fail_rate, 2),
            }
        )
    return {
        "breakers": breakers,
        "metrics": {
            **metrics.to_report_dict(),
            "recovery_time_ms": calculate_recovery_time_ms(gateway),
        },
    }


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Reliability Gateway — Live Demo</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#0e1116;--card:#171b22;--line:#262c36;--txt:#e6edf3;--mut:#8b949e;
--green:#2ea043;--red:#da3633;--amber:#d29922;--blue:#2f81f7;--pur:#a371f7}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
background:var(--bg);color:var(--txt)}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;
align-items:center;gap:12px}h1{font-size:18px;margin:0}
.sub{color:var(--mut);font-size:12px}
.wrap{display:grid;grid-template-columns:1.3fr 1fr;gap:16px;padding:20px;max-width:1200px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 12px}
input,button,select{font:inherit}
.row{display:flex;gap:8px}
input[type=text]{flex:1;background:#0d1117;border:1px solid var(--line);color:var(--txt);
padding:10px 12px;border-radius:8px}
button{background:var(--blue);border:0;color:#fff;padding:10px 16px;border-radius:8px;cursor:pointer;font-weight:600}
button.ghost{background:#21262d;color:var(--txt);border:1px solid var(--line)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{font-size:12px;padding:4px 8px;border-radius:20px;background:#0d1117;border:1px solid var(--line);
cursor:pointer;color:var(--mut)}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-weight:700;font-size:12px}
.b-cache{background:rgba(163,113,247,.15);color:var(--pur)}
.b-primary{background:rgba(46,160,67,.15);color:var(--green)}
.b-fallback{background:rgba(210,153,34,.15);color:var(--amber)}
.b-static{background:rgba(218,54,51,.15);color:var(--red)}
.resp{margin-top:14px;padding:14px;background:#0d1117;border:1px solid var(--line);border-radius:8px;min-height:70px}
.meta{display:flex;gap:16px;margin-top:10px;color:var(--mut);font-size:12px;flex-wrap:wrap}
.brk{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;
border:1px solid var(--line);border-radius:8px;margin-bottom:8px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:8px}
.s-closed{color:var(--green)}.s-closed .dot{background:var(--green)}
.s-open{color:var(--red)}.s-open .dot{background:var(--red)}
.s-half_open{color:var(--amber)}.s-half_open .dot{background:var(--amber)}
.slider{display:flex;align-items:center;gap:8px;margin-top:8px}
.slider input{flex:1}
.mgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.metric{background:#0d1117;border:1px solid var(--line);border-radius:8px;padding:10px}
.metric .v{font-size:20px;font-weight:700}.metric .k{color:var(--mut);font-size:11px;text-transform:uppercase}
.log{max-height:220px;overflow:auto;font-family:ui-monospace,monospace;font-size:12px}
.log div{padding:4px 0;border-bottom:1px solid var(--line);color:var(--mut)}
</style></head>
<body>
<header><span style="font-size:22px">🛡️</span>
<div><h1>Reliability Gateway — Live Demo</h1>
<div class="sub">cache → circuit breaker → provider fallback → static fallback</div></div></header>
<div class="wrap">
  <div>
    <div class="card">
      <h2>Send a request</h2>
      <div class="row">
        <input id="prompt" type="text" placeholder="Ask something…" value="What is the refund policy?">
        <button onclick="send()">Send</button>
        <button class="ghost" onclick="burst()">Burst ×20</button>
      </div>
      <div class="chips" id="chips"></div>
      <div class="resp" id="resp"><span class="sub">Response will appear here…</span></div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Request log</h2>
      <div class="log" id="log"></div>
    </div>
  </div>
  <div>
    <div class="card">
      <h2>Circuit breakers &nbsp;·&nbsp; chaos injection</h2>
      <div id="breakers"></div>
      <button class="ghost" style="width:100%;margin-top:6px" onclick="reset()">Reset breakers &amp; cache</button>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>Live metrics</h2>
      <div class="mgrid" id="metrics"></div>
    </div>
  </div>
</div>
<script>
const SAMPLES=["What is the refund policy?","Summarize the refund policy",
"How do I reset my password?","Explain the circuit breaker pattern",
"What are your business hours?","Summarize refund policy for 2026 deadline",
"account balance for user 123 (should bypass cache)"];
const chips=document.getElementById('chips');
SAMPLES.forEach(s=>{const c=document.createElement('span');c.className='chip';c.textContent=s;
c.onclick=()=>{document.getElementById('prompt').value=s;send()};chips.appendChild(c)});
function badge(route){if(route.startsWith('cache_hit'))return['b-cache','CACHE HIT'];
if(route==='primary')return['b-primary','PRIMARY'];if(route==='fallback')return['b-fallback','FALLBACK'];
return['b-static','STATIC FALLBACK']}
async function send(){const p=document.getElementById('prompt').value;
const r=await fetch('/api/complete',{method:'POST',headers:{'content-type':'application/json'},
body:JSON.stringify({prompt:p})});const d=await r.json();
const [cls,label]=badge(d.route);
document.getElementById('resp').innerHTML=
`<span class="badge ${cls}">${label}</span>`+
`<div style="margin-top:10px">${d.text}</div>`+
`<div class="meta"><span>provider: <b>${d.provider||'—'}</b></span>`+
`<span>latency: <b>${d.latency_ms.toFixed(0)} ms</b></span>`+
`<span>cost: <b>$${d.estimated_cost.toFixed(5)}</b></span>`+
`<span>route: <b>${d.route}</b></span>${d.error?`<span style="color:#da3633">${d.error}</span>`:''}</div>`;
const log=document.getElementById('log');const line=document.createElement('div');
line.textContent=`${new Date().toLocaleTimeString()}  ${label.padEnd(15)} ${d.latency_ms.toFixed(0)}ms  $${d.estimated_cost.toFixed(5)}`;
log.prepend(line);refresh()}
async function burst(){for(let i=0;i<20;i++){await send()}}
async function setFail(name,v){await fetch('/api/inject',{method:'POST',
headers:{'content-type':'application/json'},body:JSON.stringify({provider:name,fail_rate:parseFloat(v)})});refresh()}
async function reset(){await fetch('/api/reset',{method:'POST'});refresh();
document.getElementById('log').innerHTML=''}
async function refresh(){const d=await(await fetch('/api/state')).json();
document.getElementById('breakers').innerHTML=d.breakers.map(b=>
`<div class="brk s-${b.state}"><div><span class="dot"></span><b>${b.name}</b>
&nbsp;<span style="text-transform:uppercase;font-size:12px">${b.state}</span>
<div class="sub">fails:${b.failure_count} · opens:${b.open_count}</div></div>
<div style="min-width:130px">
<div class="slider"><span class="sub">fail</span>
<input type="range" min="0" max="1" step="0.05" value="${b.fail_rate}"
oninput="this.nextElementSibling.textContent=this.value" onchange="setFail('${b.name}',this.value)">
<b>${b.fail_rate}</b></div></div></div>`).join('');
const m=d.metrics;const cells=[['availability',(m.availability*100).toFixed(1)+'%'],
['cache hit',(m.cache_hit_rate*100).toFixed(1)+'%'],['P95 latency',m.latency_p95_ms+' ms'],
['requests',m.total_requests],['circuit opens',m.circuit_open_count],
['cost saved','$'+m.estimated_cost_saved.toFixed(3)]];
document.getElementById('metrics').innerHTML=cells.map(([k,v])=>
`<div class="metric"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('')}
refresh();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: object, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("content-length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def log_message(self, *args: object) -> None:  # silence default logging
        pass

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/index"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            self._json(_state())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/api/complete":
            prompt = str(self._body().get("prompt", "")).strip() or "hello"
            result = STATE["gateway"].complete(prompt)
            _record(result)
            self._json(
                {
                    "text": result.text,
                    "route": result.route,
                    "provider": result.provider,
                    "cache_hit": result.cache_hit,
                    "latency_ms": result.latency_ms,
                    "estimated_cost": result.estimated_cost,
                    "error": result.error,
                }
            )
        elif self.path == "/api/inject":
            data = self._body()
            name = str(data.get("provider"))
            rate = float(data.get("fail_rate", 0.0))  # type: ignore[arg-type]
            for p in STATE["gateway"].providers:
                if p.name == name:
                    p.fail_rate = rate
            self._json({"ok": True})
        elif self.path == "/api/reset":
            STATE["gateway"] = build_gateway(config)
            STATE["metrics"] = RunMetrics()
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
    print("Reliability Gateway demo running at http://localhost:8000  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
