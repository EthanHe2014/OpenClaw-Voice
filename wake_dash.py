#!/usr/bin/env python3
"""wake_dash.py — tiny progress dashboard for the hey_spark training run.
Serves a live page at http://127.0.0.1:8771 that parses wake_train.log."""
import os, re, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "wake_train.log")
STATUS = os.path.join(ROOT, "wake_train_status.json")
WORK = os.path.join(ROOT, "wake_training")
PORT = 8771

def count_files(d):
    try:
        return sum(1 for f in os.listdir(d) if f.endswith(".wav"))
    except Exception:
        return 0

def read_tail(n=400):
    try:
        with open(LOG, "r", errors="replace") as f:
            return f.readlines()[-n:]
    except Exception:
        return []

def _stages(text, stage):
    """Pipeline states AND per-stage percent, read from the live log."""
    t = text[-400000:]
    def last(pat):
        m = None
        for mm in re.finditer(pat, t): m = mm
        return m
    trn = last(r"(?i)training:\s*(\d+)%")
    feat = last(r"Computing features:\s*(\d+)/(\d+)")
    gen = last(r"(?i)Batch (\d+)/(\d+) complete")
    trn_started = bool(trn) or bool(last(r"(?i)train loss|val loss|Epoch"))
    feat_started = bool(feat) or bool(last(r"(?i)augment"))
    gen_started = bool(gen) or feat_started

    p1 = 100 if feat_started else (int(gen.group(1))*100//int(gen.group(2)) if gen else 0)
    p2 = 100 if trn_started else (int(feat.group(1))*100//int(feat.group(2)) if feat else 0)
    p3 = int(trn.group(1)) if trn else (100 if ("DONE (" in text) else 0)

    def st(pct, started, done):
        if done: return "done"
        if started: return "active"
        return "todo"
    # later stages imply earlier ones completed
    if feat_started or trn_started: p1 = 100
    if trn_started: p2 = 100
    if trn_started: feat_started = True
    s1 = st(p1, gen_started or feat_started, feat_started)
    s2 = st(p2, feat_started or trn_started, trn_started)
    s3 = st(p3, trn_started, ("DONE (" in text))
    return [
        {"name": "1 — generate clips", "state": s1, "pct": p1},
        {"name": "2 — augment clips", "state": s2, "pct": p2},
        {"name": "3 — train model", "state": s3, "pct": p3},
    ]


def snapshot():
    lines = read_tail()
    text = "".join(lines)
    def _fn():
        # newest evidence wins. Scan the tail from the END so the latest stage wins.
        order = [
            (r"(?i)training:\s*\d+%", "3 — training model"),
            (r"(?i)train loss|val loss", "3 — training model"),
            (r"Training model", "3 — training model"),
            (r"Epoch", "3 — training model"),
            (r"Computing features", "2 — augmenting / computing features"),
            (r"(?i)augment", "2 — augmenting / computing features"),
            (r"Generating negative clips", "1 — generating negatives"),
            (r"Generating positive clips", "1 — generating positives"),
            (r"STEP 3", "3 — training model"),
            (r"STEP 2", "2 — augmenting clips"),
            (r"STEP 1", "1 — generating clips"),
        ]
        # search the last ~15k chars, and pick the match closest to the end
        blob = text[-15000:]
        best = None; best_pos = -1
        for pat, name in order:
            m = None
            for mm in re.finditer(pat, blob):
                m = mm
            if m and m.start() > best_pos:
                best_pos = m.start(); best = name
        return best or "waiting for output"
    stage = _fn()
    if "=== DONE" in text or "DONE (" in text: stage = "done"
    if re.search(r"STEP[123]_FAILED", text): stage = "FAILED"

    pct = re.findall(r"(\d{1,3})%", text)
    feat = re.findall(r"Computing features:\s*(\d+)/(\d+)", text)
    epoch = re.findall(r"(?i)epoch[: ]+(\d+)\s*/\s*(\d+)", text)
    loss = re.findall(r"(?i)(train loss|val loss)[^0-9]*([0-9.]+)", text)
    err = [l.strip() for l in lines if "Error" in l or "Traceback" in l or "FAILED" in l][-5:]

    gen = os.path.join(WORK, "output", "hey_spark", "positive_train")
    n_pos = count_files(gen)

    status = {}
    try:
        status = json.load(open(STATUS))
    except Exception:
        pass

    return {
        "stage": stage,
        "percent": pct[-1] if pct else None,
        "epoch": (epoch[-1] if epoch else None),
        "loss": loss[-4:],
        "positives": n_pos,
        "stages": _stages(text, stage),
        "status": status,
        "errors": err,
        "tail": [l.rstrip("\n") for l in lines[-60:]],
        "log_size": os.path.getsize(LOG) if os.path.exists(LOG) else 0,
        "now": time.strftime("%H:%M:%S"),
    }

PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Wake training</title>
<style>
:root{--bg:#0a0e14;--fg:#d7e3f4;--cy:#22d3ee;--gr:#34d399;--rd:#f87171;--am:#fbbf24;--pan:#111826}
*{box-sizing:border-box}body{margin:0;font:16px/1.6 ui-monospace,Menlo,monospace;background:var(--bg);color:var(--fg);padding:24px 28px}
h1{font-size:18px;color:var(--cy);margin:0 0 16px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;max-width:none;width:100%}
.pan{background:var(--pan);border:1px solid #1e293b;border-radius:12px;padding:16px}
.lbl{color:#7c8aa5;font-size:12px;text-transform:uppercase;letter-spacing:.08em}
.val{font-size:24px;margin:6px 0}
.wide{grid-column:1/-1}
pre{background:#0d1420;border:1px solid #16202f;border-radius:8px;padding:12px;max-height:320px;overflow-y:auto;overflow-x:hidden;font-size:12px;white-space:pre-wrap;scrollbar-width:thin;scrollbar-color:#2a3b52 #0d1420}
#tail::-webkit-scrollbar{width:10px}
#tail::-webkit-scrollbar-track{background:#0d1420;border-radius:8px}
#tail::-webkit-scrollbar-thumb{background:#2a3b52;border-radius:8px;border:2px solid #0d1420}
#tail::-webkit-scrollbar-thumb:hover{background:#3a5170}
.stage{display:flex;align-items:center;gap:14px;margin:14px 0}
.stage .nm{flex:0 0 260px;color:#c7d3e6;font-size:16px}
.stage .sb{flex:1;height:26px;border-radius:13px;background:#0d1420;overflow:hidden;border:1px solid #1e293b}
.stage .sb > i{display:block;height:100%;border-radius:12px;transition:width .4s ease}
.stage.done .sb > i{background:linear-gradient(90deg,#0f9d58,#34d399)}
.stage.active .sb > i{background:linear-gradient(90deg,#0891b2,#22d3ee);animation:pulse 1.6s ease-in-out infinite}
.stage.todo .sb > i{background:#2a3b52}
.stage .st{flex:0 0 170px;text-align:right;font-size:14px}
.stage.done .st{color:#34d399}.stage.active .st{color:#22d3ee}.stage.todo .st{color:#7c8aa5}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.55}}
.bar{height:14px;background:#0d1420;border-radius:7px;overflow:hidden;margin-top:8px}
.fill{height:100%;width:0;background:linear-gradient(90deg,#0891b2,#22d3ee);transition:width .3s}
.err{color:var(--rd)}.ok{color:var(--gr)}
</style></head><body>
<h1>Wake word training — hey spark</h1>
<div class=grid>
  <div class=pan><div class=lbl>stage</div><div class=val id=stage>…</div>
    <div class=bar><div class=fill id=fill></div></div></div>
  <div class=pan><div class=lbl>positives generated</div><div class=val id=pos>0</div>
    <div class=lbl style="margin-top:8px">epoch</div><div id=epoch>—</div></div>
  <div class=pan><div class=lbl>loss</div><div id=loss>—</div></div>
  <div class=pan><div class=lbl>updated</div><div id=now>—</div></div>
  <div class=pan wide><div class=lbl>errors</div><pre id=err class=err>none</pre></div>
  <div class=pan wide><div class=lbl>log tail</div><pre id=tail></pre></div>
  <div class=pan wide><div class=lbl>pipeline</div><div id=pipe></div></div>
</div>
<script>
async function tick(){
  try{
    const r=await fetch('/data?'+Date.now(),{cache:'no-store'});const s=await r.json();
    document.getElementById('stage').textContent=s.stage||'—';
    document.getElementById('fill').style.width=(s.percent?s.percent+'%':'0%');
    document.getElementById('pos').textContent=s.positives;
    document.getElementById('epoch').textContent=s.epoch? (s.epoch[0]+' / '+s.epoch[1]) : '—';
    document.getElementById('loss').textContent=(s.loss&&s.loss.length)? s.loss.map(x=>x[0]+': '+x[1]).join('   ') : '—';
    document.getElementById('now').textContent=s.now;
    const stages=s.stages||[];
    const pipe=document.getElementById('pipe');
    const CLS={done:'done',active:'active',todo:'todo'};
    const lbl={done:'done',active:'running',todo:'waiting'};
    const html=stages.map(function(x){
      const pct=Math.max(0,Math.min(100,x.pct==null?0:x.pct));
      const st=CLS[x.state]||'todo';
      return '<div class="stage '+st+'">'
        +'<span class="nm">'+x.name+'</span>'
        +'<span class="sb"><i style="width:'+pct+'%"></i></span>'
        +'<span class="st">'+pct+'% '+lbl[x.state]+'</span>'
        +'</div>';
    }).join('');
    if(pipe._h!==html){ pipe.innerHTML=html; pipe._h=html; }
    document.getElementById('err').textContent=(s.errors&&s.errors.length)? s.errors.join(String.fromCharCode(10)) : 'none';
    const tailEl=document.getElementById('tail');
    const atBottom=(tailEl.scrollHeight-tailEl.scrollTop-tailEl.clientHeight)<40;
    tailEl.textContent=(s.tail||[]).join(String.fromCharCode(10));
    if(atBottom){ tailEl.scrollTop=tailEl.scrollHeight; }
  }catch(e){ document.getElementById('now').textContent='disconnected: '+e.message; }
}
setInterval(tick,1500);tick();
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        path = self.path.split("?",1)[0]
        if path == "/data":
            body = json.dumps(snapshot()).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        else:
            body = PAGE.encode()
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)

if __name__ == "__main__":
    print(f"Wake training dashboard: http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
