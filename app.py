import os, io, json, hmac, sqlite3, hashlib, time, threading
from datetime import datetime, timezone
from pathlib import Path
import qrcode
from flask import Flask, request, jsonify, send_file, session, Response

app = Flask(__name__)
secret = os.environ.get("SESSION_SECRET", "")
if len(secret) < 32:
    raise RuntimeError("SESSION_SECRET must be at least 32 characters")
app.secret_key = secret
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=True,
                  SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=28800)
DB = Path(os.environ.get("DB_PATH", "/tmp/onrf2_test.sqlite"))
DB.parent.mkdir(parents=True, exist_ok=True)
SAMPLES = [
    ("TEST-W-0001", "u_SWdQFZB3G6iriCcC7WVbVa31N8tqLS", "WHITE", "unused"),
    ("TEST-M-00001", "gMCHnRctQhsNllxdpRXlexj_Duwnnt7D", "MAIN", "unused"),
    ("TEST-VC-001", "ydLYtQTYalsp2HK2Dj8Sr_U3ObOKhOOf", "VIP CREATIVITY", "unused"),
    ("TEST-VL-001", "gm9tvk2g8bI4G73KT7bhd5hhJHIU2yKh", "VIP CULTURE", "unused"),
    ("TEST-M-00002", "xKsucOkJNmo69lGjXS1nVyKU3GScOdE_", "MAIN", "used"),
    ("TEST-W-0002", "9VF7DBtRkoXzp-KbZcSAWXlyFF2Qd4kQ", "WHITE", "cancelled"),
    ("TEST-INVALID-001", "TEST-INVALID-QR-001-REJECT", "INVALID", "invalid"),
    ("TEST-M-00001 STUB", "gMCHnRctQhsNllxdpRXlexj_Duwnnt7D", "MAIN STUB", "same-as-02")
]
RATELIMIT = {}
LOCK = threading.Lock()

def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def connect():
    db=sqlite3.connect(str(DB), timeout=15)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    return db

def hashed(v):
    return hashlib.sha256(v.encode("utf-8")).hexdigest()

def reset_data(db):
    db.execute("DELETE FROM scans")
    db.execute("DELETE FROM tickets")
    for serial, token, kind, status in SAMPLES[:6]:
        used = "2026-10-15T17:00:00+00:00" if status=="used" else None
        gate = "GATE A" if status=="used" else None
        user = "DEMO STAFF" if status=="used" else None
        db.execute("INSERT INTO tickets(serial,code_hash,kind,status,first_time,first_gate,first_user) VALUES (?,?,?,?,?,?,?)",
                   (serial,hashed(token),kind,status,used,gate,user))

def init_db():
    with connect() as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS tickets(
            serial TEXT UNIQUE NOT NULL, code_hash TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL, status TEXT NOT NULL,
            first_time TEXT, first_gate TEXT, first_user TEXT);
            CREATE TABLE IF NOT EXISTS scans(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL, serial TEXT, outcome TEXT NOT NULL,
            gate TEXT NOT NULL, operator TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_code ON tickets(code_hash);""")
        n=db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        if not n:
            reset_data(db)
        db.commit()
init_db()

@app.after_request
def headers(resp):
    resp.headers["Cache-Control"]="no-store"
    resp.headers["X-Content-Type-Options"]="nosniff"
    resp.headers["Referrer-Policy"]="no-referrer"
    resp.headers["X-Frame-Options"]="DENY"
    resp.headers["Permissions-Policy"]="camera=(self), microphone=()"
    return resp

def error(msg, status=400):
    return jsonify(ok=False, message=msg), status

def user_required(admin=False, write=False):
    role=session.get("role")
    if role not in ("admin","staff"):
        return error("Please sign in first",401)
    if admin and role!="admin":
        return error("Administrator only",403)
    if write and not hmac.compare_digest(str(request.headers.get("X-CSRF","")),str(session.get("csrf",""))):
        return error("Security token missing",403)
    return None

@app.get("/health")
def health():
    with connect() as db:
        db.execute("SELECT 1")
    return jsonify(status="ok", demo=True)

@app.get("/")
def index():
    return Response(PAGE, mimetype="text/html")

@app.post("/api/login")
def login():
    x=request.get_json(silent=True) or {}
    name=str(x.get("username","")).strip().lower()
    pw=str(x.get("password",""))
    ip=request.headers.get("CF-Connecting-IP",request.remote_addr or "unknown")
    key=ip[:50]+":"+name[:30]
    with LOCK:
        attempts=[t for t in RATELIMIT.get(key,[]) if time.time()-t<300]
        if len(attempts)>=12: return error("Too many attempts; retry in five minutes",429)
        attempts.append(time.time()); RATELIMIT[key]=attempts
    role = "admin" if name=="admin" else "staff" if name=="staff" else ""
    envname = "ADMIN_PASSWORD" if role=="admin" else "STAFF_PASSWORD"
    expected = os.environ.get(envname,"")
    if not expected or len(expected)<12 or not hmac.compare_digest(pw,expected):
        return error("Incorrect username or password",401)
    session.clear()
    session.update(role=role,username=name,csrf=os.urandom(24).hex())
    session.permanent=True
    return jsonify(ok=True,role=role,csrf=session["csrf"])

@app.get("/api/me")
def me():
    err=user_required()
    if err:return err
    return jsonify(ok=True, role=session["role"], username=session["username"],csrf=session["csrf"],demo=True)

@app.post("/api/logout")
def logout():
    err=user_required(write=True)
    if err:return err
    session.clear()
    return jsonify(ok=True)

@app.post("/api/scan")
def scan():
    err=user_required(write=True)
    if err:return err
    x=request.get_json(silent=True) or {}
    token=str(x.get("code","")).strip()
    gate=str(x.get("gate","MAIN GATE")).strip()[:60] or "MAIN GATE"
    if len(token)<8 or len(token)>256:
        return error("Invalid QR content",400)
    digest=hashed(token)
    stamp=utc()
    db=connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        r=db.execute("UPDATE tickets SET status='used', first_time=?,first_gate=?,first_user=? "
                     "WHERE code_hash=? AND status='unused' RETURNING serial,kind,status,first_time,first_gate,first_user",
                    (stamp,gate,session["username"],digest)).fetchone()
        if r:
            outcome="ACCESS GRANTED"; reason="VALID FIRST ENTRY"; row=dict(r)
        else:
            r=db.execute("SELECT serial,kind,status,first_time,first_gate,first_user FROM tickets WHERE code_hash=?",(digest,)).fetchone()
            row=dict(r) if r else None
            outcome="ALREADY SCANNED" if r and r["status"]=="used" else "ACCESS DENIED"
            reason="ALREADY USED" if outcome=="ALREADY SCANNED" else ("CANCELLED" if r else "INVALID TICKET")
        db.execute("INSERT INTO scans(time,serial,outcome,gate,operator) VALUES (?,?,?,?,?)",
                   (stamp, row["serial"] if row else None,outcome,gate,session["username"]))
        db.commit()
        return jsonify(ok=True,result=outcome,reason=reason,ticket=row,checked_at=stamp)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@app.get("/api/samples")
def samples():
    err=user_required()
    if err:return err
    return jsonify(ok=True,samples=[{"i":i+1,"serial":s,"kind":k,"state":st,
                  "qr":"/api/sample/"+str(i+1)} for i,(s,t,k,st) in enumerate(SAMPLES)])

@app.get("/api/sample/<int:i>")
def sample_qr(i):
    err=user_required()
    if err:return err
    if not (1<=i<=len(SAMPLES)): return error("Not found",404)
    qr=qrcode.QRCode(box_size=8,border=4,error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(SAMPLES[i-1][1]);qr.make(fit=True)
    buf=io.BytesIO()
    qr.make_image(fill_color="black",back_color="white").save(buf,format="PNG");buf.seek(0)
    return send_file(buf,mimetype="image/png")

@app.get("/api/summary")
def summary():
    err=user_required(admin=True)
    if err:return err
    with connect() as db:
        rows=db.execute("SELECT serial,kind,status,first_time,first_gate,first_user FROM tickets ORDER BY serial").fetchall()
        logs=db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 30").fetchall()
    return jsonify(ok=True,tickets=[dict(r) for r in rows],logs=[dict(r) for r in logs])

@app.post("/api/reset")
def reset():
    err=user_required(admin=True,write=True)
    if err:return err
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        reset_data(db);db.commit()
    return jsonify(ok=True,message="TEST DATA RESET")

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#270d15"><title>ONRF2 | Festival Access</title>
<style>
:root{--bg:#210e13;--deep:#35141c;--wine:#66192c;--gold:#f4bd46;--cream:#fff5da;--green:#19a775;--red:#fb5367}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 100% 0,#6d2935 0%,transparent 48%),var(--bg);color:var(--cream);font:15px/1.5 system-ui,-apple-system,Segoe UI,Arial;min-height:100vh}.texture{position:fixed;inset:0;opacity:.14;pointer-events:none;background-image:repeating-radial-gradient(circle at 15% 30%,transparent 0 14px,#e3a956 15px 17px,transparent 18px 32px),repeating-linear-gradient(60deg,transparent 0 34px,#edaa4f 35px 37px,transparent 38px 70px);mask-image:linear-gradient(to bottom,#000,transparent 90%)}.wrap{position:relative;max-width:1080px;margin:auto;padding:26px 18px 100px}.top{display:flex;align-items:center;justify-content:space-between;gap:10px}.brand{font-weight:950;font-size:26px;letter-spacing:-1px}.brand b{color:var(--gold)}.tag{border:1px solid #c79250;border-radius:20px;font-size:11px;font-weight:850;letter-spacing:1.8px;padding:7px 10px;color:var(--gold)}.hero{text-align:center;margin:30px 0 24px}.hero small{color:var(--gold);font-size:11px;font-weight:850;letter-spacing:3px}.hero h1{font-size:clamp(37px,7vw,74px);line-height:1.05;margin:8px 0 10px;font-weight:950;letter-spacing:-2px}.hero h1 span{color:var(--gold);font-style:italic}.hero p{color:#ddcabb;margin:0}.panel{border:1px solid #94624477;border-radius:24px;padding:22px;background:linear-gradient(145deg,#4a2229ef,#280f18f9);box-shadow:0 16px 45px #0004}.max420{max-width:440px;margin:20px auto}.eyebrow{color:var(--gold);font-size:11px;font-weight:850;letter-spacing:2px}.field{width:100%;background:#fffaf0;color:#28171d;border:2px solid transparent;border-radius:13px;padding:15px;font-size:16px;margin:7px 0 13px}.field:focus{outline:none;border-color:var(--gold)}label{font-size:12px;letter-spacing:1px;font-weight:750}.btn{border:0;cursor:pointer;background:var(--gold);color:#221015;border-radius:13px;font-weight:900;padding:14px 18px;font-size:14px;min-height:48px}.btn:disabled{opacity:.5}.btn.subtle{background:#ffffff16;color:var(--cream);border:1px solid #a98c7b66}.btn.danger{background:#8f2940;color:white}.btn.full{width:100%}.actions{display:flex;flex-wrap:wrap;gap:10px}.nav{display:flex;gap:8px;margin:20px 0}.nav .btn{flex:1}.muted{color:#c4abac;font-size:13px}.hidden{display:none!important}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}.camera{min-height:240px;background:#160a0f;border:1px dashed #a8794c;border-radius:18px;margin:12px 0;display:grid;place-items:center;overflow:hidden;position:relative}.camera .corners{width:142px;height:142px;border:4px solid var(--gold);border-radius:21px;box-shadow:0 0 24px #d69d5344}.camera .vibe{position:absolute;bottom:10px;color:#d4b8a1;font-size:12px}.camera video{object-fit:cover}.result{border-radius:20px;background:#24151d;padding:27px 16px;min-height:260px;text-align:center;border:2px solid #70505a;display:flex;flex-direction:column;justify-content:center;align-items:center}.result.good{background:#063f31;border-color:#31e3a6}.result.bad{background:#631827;border-color:#ff7c91}.result h2{font-size:clamp(25px,4vw,36px);line-height:1.15;margin:12px 0}.result .symbol{font-size:52px}.detail{font-size:14px;line-height:1.9}.scan-form{display:flex;gap:8px}.scan-form input{min-width:0;flex:1;margin:0}.gate{display:flex;align-items:center;gap:10px}.gate .field{width:160px;margin:0}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.ticket{padding:15px;background:linear-gradient(135deg,#75402f,#cf9239);border:2px solid #e3b75b;border-radius:16px;color:#fff;min-width:0}.ticket.white{background:linear-gradient(135deg,#fff6df,#f0d9a5);color:#4a1c28}.ticket.red{background:linear-gradient(135deg,#82203a,#571326)}.ticket.vip{background:linear-gradient(135deg,#a76c15,#e9b747)}.ticket.culture{background:linear-gradient(135deg,#1b2820,#343120)}.ticket img{display:block;width:min(180px,85%);max-width:100%;margin:12px auto;border:6px solid white}.ticket strong{font-size:13px;overflow-wrap:anywhere}.ticket .btn{min-height:39px;padding:8px 10px;font-size:11px;width:100%}.message{margin:12px 0;color:var(--gold);min-height:22px}.small{font-size:12px}.footer{text-align:center;opacity:.7;font-size:11px;letter-spacing:1px;margin:35px 0}@media(max-width:730px){.wrap{padding:15px 12px 70px}.grid{grid-template-columns:1fr}.panel{padding:17px}.hero{margin:19px 0}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.brand{font-size:22px}.top{flex-wrap:wrap}.gate .field{width:130px}.scan-form{flex-wrap:wrap}.scan-form button{width:100%}.camera{min-height:290px}}@media(max-width:390px){.cards{grid-template-columns:1fr}}
</style></head><body><div class="texture"></div><div class="wrap"><header class="top"><div class="brand">🦁 ONE <b>NATION</b> · VOL.2</div><div class="tag">TEST ENVIRONMENT</div></header>
<div class="hero"><small>ONE LOVE · ONE VIBE · SHARED RHYTHM</small><h1>REGGAE <span>FESTIVAL.</span></h1><p>Official-style ticket verification · Testing portal</p></div>
<div id="login" class="panel max420"><div class="eyebrow">SECURE STAFF ACCESS</div><h2>Check-in Login</h2><form id="loginForm"><label>USERNAME</label><input class="field" id="user" value="admin" autocomplete="username"><label>PASSWORD</label><input class="field" id="password" type="password" placeholder="Enter password" autocomplete="current-password" required><button class="btn full">OPEN CHECK-IN →</button></form><div class="message" id="loginMsg"></div><p class="muted">Test site only. Real tickets cannot be used here.</p></div>
<div id="app" class="hidden"><div class="top"><div><span class="eyebrow">ONE NATION CONTROL DESK</span><h2 style="margin:4px 0">Welcome, <span id="who"></span></h2></div><button class="btn subtle" id="signout">Sign out</button></div><nav class="nav"><button class="btn" data-tab="scan">▣ SCANNER</button><button class="btn subtle" data-tab="cards">▦ TEST QR CODES</button><button class="btn subtle hidden" id="adminTab" data-tab="admin">⚙ ADMIN</button></nav>
<section id="scanPage" class="grid"><div class="panel"><div class="eyebrow">GATE CONTROL / ACTIVE</div><h2>Scan ticket QR</h2><div class="camera" id="camera"><div id="cameraSlot" class="hidden" style="width:100%"></div><div id="cameraHelp"><div class="corners"></div><div class="vibe">PLACE QR INSIDE FRAME</div></div></div><div class="actions"><button class="btn" id="startCamera">▣ Start camera</button><button class="btn subtle" id="stopCamera">Stop</button></div><p class="message small" id="cameraMsg"></p><div class="gate"><label>GATE</label><input class="field" id="gate" value="MAIN GATE" maxlength="60"></div><p class="muted">Or use a QR scanner / paste raw QR content:</p><form class="scan-form" id="scanForm"><input class="field" id="code" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Scan or enter token"><button class="btn">VERIFY</button></form></div><div class="panel"><div class="eyebrow">LIVE VERIFICATION RESULT</div><div class="result" id="result"><div class="symbol" id="symbol">✳</div><h2 id="outcome">READY TO SCAN</h2><div class="detail" id="details">Waiting for first test ticket.</div></div><p class="muted">One ticket = one entry. Main ticket and stub use the same QR code.</p></div></section>
<section id="cardsPage" class="panel hidden"><span class="eyebrow">SAMPLES / TEST ONLY</span><h2>8 Test QR Codes</h2><p class="muted">Scan the QR with another device, or tap “Simulate scan” to test on one phone. Card #8 repeats code #2.</p><div class="cards" id="cards"></div></section>
<section id="adminPage" class="panel hidden"><span class="eyebrow">AUTHORIZED ADMIN ONLY</span><h2>Recent scan activity</h2><p class="muted">Demo resets do not affect any official production tickets.</p><div class="actions"><button class="btn" id="reload">Refresh logs</button><button class="btn danger" id="reset">Reset TEST data</button></div><div id="logs" class="small" style="margin-top:17px;overflow-x:auto"></div></section></div>
<div class="footer">ONE NATION REGGAE FESTIVAL VOL.2 · TEST ONLY · NOT VALID FOR ENTRY</div></div>
<script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script><script>
let csrf='',role='',scanner=null,scanning=false,lastCode='',lastTime=0;const $=id=>document.getElementById(id);
const sampleCodes=['u_SWdQFZB3G6iriCcC7WVbVa31N8tqLS','gMCHnRctQhsNllxdpRXlexj_Duwnnt7D','ydLYtQTYalsp2HK2Dj8Sr_U3ObOKhOOf','gm9tvk2g8bI4G73KT7bhd5hhJHIU2yKh','xKsucOkJNmo69lGjXS1nVyKU3GScOdE_','9VF7DBtRkoXzp-KbZcSAWXlyFF2Qd4kQ','TEST-INVALID-QR-001-REJECT','gMCHnRctQhsNllxdpRXlexj_Duwnnt7D'];
async function api(path,method='GET',body=null){let r=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF':csrf},body:body?JSON.stringify(body):undefined});let v=await r.json().catch(()=>({message:'Unable to connect to server'}));if(!r.ok)throw Error(v.message||'Request failed');return v;}
function time(v){if(!v)return '—';try{return new Intl.DateTimeFormat('en-GB',{timeZone:'Africa/Freetown',dateStyle:'medium',timeStyle:'medium'}).format(new Date(v))+' (Freetown)'}catch(e){return v}}
function signedIn(data){csrf=data.csrf;role=data.role;$('login').classList.add('hidden');$('app').classList.remove('hidden');$('who').textContent=role.toUpperCase();$('adminTab').classList.toggle('hidden',role!=='admin');tab('scan')}
async function restore(){try{signedIn(await api('/api/me'))}catch(e){}}
$('loginForm').onsubmit=async e=>{e.preventDefault();$('loginMsg').textContent='Signing in…';try{let v=await api('/api/login','POST',{username:$('user').value,password:$('password').value});signedIn(v);$('password').value=''}catch(e){$('loginMsg').textContent=e.message}};
$('signout').onclick=async()=>{try{await api('/api/logout','POST',{})}catch(e){}location.reload()};
function tab(name){if(name!=='scan')stopCamera();for(const n of ['scan','cards','admin'])$(n+'Page').classList.toggle('hidden',n!==name);document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('subtle',x.dataset.tab!==name));if(name==='cards')loadCards();if(name==='admin')loadLogs();}
document.querySelectorAll('[data-tab]').forEach(x=>x.onclick=()=>tab(x.dataset.tab));
function show(v){let ok=v.result==='ACCESS GRANTED',dup=v.result==='ALREADY SCANNED';$('result').className='result '+(ok?'good':'bad');$('symbol').textContent=ok?'✓':'✕';$('outcome').textContent=v.result;let t=v.ticket,parts=[v.reason];if(t){parts.push('Ticket: '+t.serial, 'Type: '+t.kind);if(dup){parts.push('First entry: '+time(t.first_time),'Gate: '+(t.first_gate||'—'),'Staff: '+(t.first_user||'—'))} else if(ok){parts.push('Entry time: '+time(t.first_time))}}$('details').textContent=parts.join('\n');$('details').style.whiteSpace='pre-line'}
async function verify(code){code=(code||'').trim();if(!code)return;try{show(await api('/api/scan','POST',{code,gate:$('gate').value||'MAIN GATE'}))}catch(e){$('result').className='result bad';$('symbol').textContent='!';$('outcome').textContent='SCAN ERROR';$('details').textContent=e.message} $('code').value=''}
$('scanForm').onsubmit=e=>{e.preventDefault();verify($('code').value)};
$('startCamera').onclick=async()=>{if(scanning)return; if(!window.isSecureContext){$('cameraMsg').textContent='HTTPS required for camera access';return}if(typeof Html5Qrcode==='undefined'){$('cameraMsg').textContent='Camera library unavailable; use manual input or USB scanner';return}try{$('cameraHelp').classList.add('hidden');$('cameraSlot').classList.remove('hidden');scanner=new Html5Qrcode('cameraSlot');await scanner.start({facingMode:'environment'},{fps:10,qrbox:{width:210,height:210}},(value)=>{let now=Date.now();if(value===lastCode&&now-lastTime<1200)return;lastCode=value;lastTime=now;verify(value)},()=>{});scanning=true;$('cameraMsg').textContent='Camera active'}catch(e){$('cameraMsg').textContent='Cannot start camera: '+e.message;await stopCamera()}};
async function stopCamera(){if(scanner){try{if(scanning)await scanner.stop()}catch(e){}try{scanner.clear()}catch(e){}}scanner=null;scanning=false;$('cameraSlot').classList.add('hidden');$('cameraHelp').classList.remove('hidden');$('cameraMsg').textContent=''}
$('stopCamera').onclick=stopCamera;
async function loadCards(){try{let v=await api('/api/samples');let box=$('cards');box.innerHTML='';v.samples.forEach(s=>{let card=document.createElement('div');card.className='ticket '+(s.kind.includes('VIP CULTURE')?'culture':s.kind.includes('VIP')?'vip':s.kind.includes('WHITE')?'white':'red');let a=document.createElement('strong');a.textContent=s.serial;card.append(a);let p=document.createElement('div');p.className='small';p.textContent=s.kind+' · '+s.state;card.append(p);let img=document.createElement('img');img.src=s.qr;img.alt='QR code for '+s.serial;card.append(img);let button=document.createElement('button');button.className='btn';button.textContent='SIMULATE SCAN';button.onclick=()=>{tab('scan');verify(sampleCodes[s.i-1])};card.append(button);box.append(card)})}catch(e){$('cards').textContent=e.message}}
async function loadLogs(){try{let v=await api('/api/summary');let node=$('logs');node.replaceChildren();for(const r of v.logs){let p=document.createElement('p');p.style.borderBottom='1px solid #ffffff22';p.textContent=time(r.time)+' · '+(r.serial||'UNKNOWN')+' · '+r.outcome+' · '+r.gate;node.append(p)}}catch(e){$('logs').textContent=e.message}}
$('reload').onclick=loadLogs;$('reset').onclick=async()=>{if(!confirm('Reset all TEST tickets and logs?'))return;try{await api('/api/reset','POST',{});alert('Test data reset successfully');loadLogs()}catch(e){alert(e.message)}};
restore();
</script></body></html>"""

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
