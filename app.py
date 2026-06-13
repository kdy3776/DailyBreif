#!/usr/bin/env python3
"""
데일리 투자 브리핑 — 웹 플랫폼 (FastAPI)
------------------------------------------------------------
하나의 상시 FastAPI 앱이:
  · 대시보드(최신 브리핑) + 날짜별 아카이브
  · 💬 물어보기(실시간 Q&A, Claude + 웹서치)
  · 🔮 전망(3개월/6개월 시나리오)
  · ⏰ 내장 스케줄러: 매일 브리핑 생성→저장→이메일, 매월 1일 전망 생성
저장은 SQLite(영구 디스크). 기존 daily_briefing / outlook 로직 재사용.

환경변수: ANTHROPIC_API_KEY, SMTP_*, EMAIL_*, (선택) FRED_API_KEY, BRIEFING_TZ,
          DATA_DIR(기본 ./data), DAILY_HOUR(기본 6), DAILY_MINUTE(기본 13),
          APP_USER/APP_PASSWORD(설정 시 사이트 접근에 Basic 인증)
"""

import os
import shutil
import sqlite3
import secrets
import base64
import datetime
from zoneinfo import ZoneInfo

import anthropic
import markdown as md
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from daily_briefing import (
    generate_briefing, send_email, _macro_table_md,
    MODEL, WEB_SEARCH_TOOL, TIMEZONE,
)
from macro_charts import build_macro_dashboard
from outlook import generate_outlook

DATA_DIR = os.environ.get("DATA_DIR", "./data")
STATIC_DIR = os.path.join(DATA_DIR, "static")
DB_PATH = os.path.join(DATA_DIR, "app.db")
os.makedirs(STATIC_DIR, exist_ok=True)

MD_EXT = ["extra", "sane_lists", "tables"]


# ──────────────────────────── 저장소 (SQLite) ────────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS briefings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, date_label TEXT, content_md TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS outlooks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, date_label TEXT, content_md TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS qas(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, date_label TEXT, question TEXT, answer TEXT)""")


def _now():
    return datetime.datetime.now(ZoneInfo(TIMEZONE))


def save_row(table, content_md):
    now = _now()
    with db() as c:
        cur = c.execute(
            f"INSERT INTO {table}(created_at, date_label, content_md) VALUES (?,?,?)",
            (now.isoformat(), now.strftime("%Y-%m-%d %H:%M"), content_md))
        return cur.lastrowid


def get_row(table, rid):
    with db() as c:
        r = c.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


def latest_row(table):
    with db() as c:
        r = c.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None


def list_rows(table, limit=60):
    with db() as c:
        rs = c.execute(
            f"SELECT id, date_label FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rs]


def save_qa(question, answer):
    now = _now()
    with db() as c:
        c.execute("INSERT INTO qas(created_at, date_label, question, answer) VALUES (?,?,?,?)",
                  (now.isoformat(), now.strftime("%m-%d %H:%M"), question, answer))


def list_qas(limit=40):
    with db() as c:
        rs = c.execute(
            "SELECT date_label, question, answer FROM qas ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rs]


# ──────────────────────────── 생성 작업 (진행상황 추적) ────────────────────────────
import threading
import uuid
import time

JOBS = {}  # job_id -> 상태 dict


def _set(jid, **kw):
    if jid and jid in JOBS:
        JOBS[jid].update(kw)


def run_daily_job(jid=None):
    """브리핑 생성 → 거시 그래프 → 이메일 발송 → 웹용 저장. (jid 주면 단계 보고)"""
    _set(jid, stage="① 브리핑 작성 중 (웹서치, 1~2분)", step=1)
    print("[JOB] 데일리 브리핑 생성 시작")
    briefing = generate_briefing()

    _set(jid, stage="② 거시 그래프 생성", step=2)
    chart_path, rows = None, []
    try:
        chart_path, rows = build_macro_dashboard(os.path.join(DATA_DIR, "macro_tmp.png"))
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 거시 그래프 실패: {e}")

    _set(jid, stage="③ 이메일 발송", step=3)
    try:
        send_email(briefing, chart_path, rows)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 이메일 발송 실패(웹 저장은 계속): {e}")

    _set(jid, stage="④ 저장", step=4)
    web_md = briefing.replace("{{MACRO_TABLE}}", _macro_table_md(rows) or "")
    if chart_path and os.path.exists(chart_path):
        fname = f"macro_{_now().strftime('%Y%m%d_%H%M%S')}.png"
        shutil.copy(chart_path, os.path.join(STATIC_DIR, fname))
        web_md = web_md.replace("{{MACRO_CHART}}", f"![Macro Dashboard](/static/{fname})")
    else:
        web_md = web_md.replace("{{MACRO_CHART}}", "")
    rid = save_row("briefings", web_md)
    print(f"[JOB] 저장 완료 id={rid}")
    return rid


def run_outlook_job(jid=None):
    _set(jid, stage="① 전망 분석 중 (웹서치, 2~3분)", step=1)
    print("[JOB] 전망 생성 시작")
    out = generate_outlook()
    _set(jid, stage="② 저장", step=2)
    rid = save_row("outlooks", out)
    print(f"[JOB] 전망 저장 id={rid}")
    return rid


def start_job(kind, target, total):
    """백그라운드 스레드로 실행하고 job_id 반환."""
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"kind": kind, "stage": "시작", "step": 0, "total": total,
                 "status": "running", "error": None, "result_id": None, "started": time.time()}

    def runner():
        try:
            rid = target(jid)
            _set(jid, status="done", result_id=rid, stage="✅ 완료", step=total)
        except Exception as e:  # noqa: BLE001
            print(f"[JOB] 실패: {e}")
            _set(jid, status="error", error=str(e), stage="⚠️ 오류")

    threading.Thread(target=runner, daemon=True).start()
    return jid


# ──────────────────────────── 채팅 (Q&A) ────────────────────────────
def answer_question(question: str) -> str:
    """최신 브리핑을 컨텍스트로 삼아 웹서치 포함 답변."""
    client = anthropic.Anthropic()
    latest = latest_row("briefings")
    context = latest["content_md"][:6000] if latest else "(아직 생성된 브리핑이 없습니다)"
    system = (
        "당신은 사용자의 투자 리서치 어시스턴트입니다. 한국어로 간결하고 정확하게 답하세요. "
        "아래 '최신 브리핑'을 참고 컨텍스트로 쓰되, 최신 시세·뉴스가 필요하면 웹서치로 확인하세요. "
        "당신은 투자자문가가 아니며, 단정적 매수·매도 추천이나 수익 보장은 하지 않습니다. "
        "수치에는 기준 시점을 표기하세요."
    )
    user = f"[최신 브리핑 발췌]\n{context}\n\n[질문]\n{question}"
    resp = client.messages.create(
        model=MODEL, max_tokens=2000, system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 5,
                "user_location": {"type": "approximate", "country": "KR", "timezone": TIMEZONE}}],
    )
    parts = [b.text for b in resp.content if b.type == "text"]
    return "\n".join(p for p in parts if p).strip() or "(답변을 생성하지 못했습니다.)"


# ──────────────────────────── 인증 (선택) ────────────────────────────
def require_auth(request: Request):
    user = os.environ.get("APP_USER")
    pw = os.environ.get("APP_PASSWORD")
    if not pw:  # 비밀번호 미설정 → 공개
        return
    header = request.headers.get("Authorization", "")
    if header.startswith("Basic "):
        try:
            u, _, p = base64.b64decode(header[6:]).decode().partition(":")
            if secrets.compare_digest(u, user or u) and secrets.compare_digest(p, pw):
                return
        except Exception:  # noqa: BLE001
            pass
    raise HTTPException(status_code=401, detail="Auth required",
                        headers={"WWW-Authenticate": "Basic"})


# ──────────────────────────── HTML 렌더 ────────────────────────────
CSS = """
:root{--ac:#6366f1;--bg:#eef1f6;--card:#fff;--ink:#1e293b;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:-apple-system,'Segoe UI',Roboto,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;}
header{background:linear-gradient(135deg,#1e293b,#4338ca);color:#fff;padding:16px 22px;}
header h1{margin:0;font-size:18px;letter-spacing:-.2px}
nav{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
nav a{color:#e0e7ff;text-decoration:none;font-size:13px;padding:6px 12px;border-radius:999px;background:rgba(255,255,255,.12)}
nav a.active{background:#fff;color:#1e293b;font-weight:600}
.wrap{max-width:1080px;margin:18px auto;padding:0 14px;display:grid;grid-template-columns:220px 1fr;gap:16px}
@media(max-width:760px){.wrap{grid-template-columns:1fr}}
.side{background:var(--card);border-radius:14px;padding:12px;height:fit-content;box-shadow:0 2px 10px rgba(15,23,42,.06)}
.side h3{font-size:12px;color:#64748b;margin:6px 6px 10px;text-transform:uppercase;letter-spacing:.5px}
.side a{display:block;padding:8px 10px;border-radius:8px;color:#334155;text-decoration:none;font-size:13px}
.side a:hover{background:#f1f5f9}
.card{background:var(--card);border-radius:16px;padding:24px;box-shadow:0 2px 14px rgba(15,23,42,.07)}
.card h1{font-size:21px}
.card h2{font-size:16.5px;border-left:4px solid var(--ac);padding-left:10px;margin-top:26px}
.card h3{font-size:14.5px;background:#f8fafc;border:1px solid #e7ebf2;border-left:4px solid var(--ac);border-radius:10px;padding:10px 13px}
.card table{width:100%;border-collapse:collapse;margin:10px 0;font-size:12.5px}
.card th{background:#1e293b;color:#fff;text-align:left;padding:8px 10px}
.card td{padding:8px 10px;border-bottom:1px solid #eef2f7;vertical-align:top}
.card tr:nth-child(even) td{background:#f8fafc}
.card blockquote{background:#eef2ff;border-left:4px solid var(--ac);border-radius:10px;padding:10px 14px;color:#3730a3;font-size:13px}
.card img{max-width:100%;border:1px solid #e7ebf2;border-radius:12px}
.card a{color:#4338ca;word-break:break-all}
#log{display:flex;flex-direction:column;gap:10px;margin-bottom:14px}
.msg{padding:11px 14px;border-radius:12px;font-size:14px;line-height:1.6;max-width:90%;white-space:pre-wrap}
.me{align-self:flex-end;background:var(--ac);color:#fff}
.ai{align-self:flex-start;background:#f1f5f9}
.askbar{display:flex;gap:8px}
.askbar input{flex:1;padding:12px 14px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px}
.askbar button{padding:12px 18px;border:0;border-radius:10px;background:var(--ac);color:#fff;font-weight:600;cursor:pointer}
button{padding:10px 16px;border:0;border-radius:10px;background:var(--ac);color:#fff;font-weight:600;cursor:pointer}
.jobbox{display:none;margin-top:12px;padding:12px 14px;border-radius:10px;background:#f1f5f9;
 border:1px solid #e2e8f0;font-size:13px;color:#334155}
.muted{color:#94a3b8;font-size:12px}
/* 드래그 선택 → 즉시 질문 */
#selask{position:absolute;display:none;z-index:50;background:#1e293b;color:#fff;border:0;
 border-radius:8px;padding:7px 12px;font-size:13px;font-weight:600;cursor:pointer;
 box-shadow:0 4px 14px rgba(0,0,0,.25)}
#popask{position:fixed;right:18px;bottom:18px;width:min(380px,92vw);max-height:70vh;display:none;
 z-index:60;background:#fff;border:1px solid #e2e8f0;border-radius:14px;
 box-shadow:0 10px 40px rgba(15,23,42,.22);overflow:hidden;flex-direction:column}
#popask .ph{background:linear-gradient(135deg,#1e293b,#4338ca);color:#fff;padding:10px 14px;
 font-size:13px;font-weight:600;display:flex;justify-content:space-between;align-items:center}
#popask .ph span{cursor:pointer;opacity:.8}
#popask .pb{padding:12px 14px;overflow-y:auto;font-size:13.5px;line-height:1.6}
#popask .q{color:#64748b;font-size:12px;border-left:3px solid var(--ac);padding-left:8px;margin-bottom:10px}
"""

PROGRESS_JS = """
<script>
function _fmt(s){let m=Math.floor(s/60),x=s%60;return (m<10?'0':'')+m+':'+(x<10?'0':'')+x;}
function _ensureBox(){
  let b=document.getElementById('jobbox');
  if(!b){
    b=document.createElement('div'); b.id='jobbox'; b.className='jobbox';
    b.style.position='fixed'; b.style.top='70px'; b.style.left='50%';
    b.style.transform='translateX(-50%)'; b.style.zIndex='80'; b.style.maxWidth='90vw';
    b.style.boxShadow='0 6px 20px rgba(0,0,0,.15)';
    document.body.appendChild(b);
  }
  b.style.display='block'; return b;
}
let _timer=null;
function pollJob(id){
  if(_timer) clearInterval(_timer);
  let box=_ensureBox();
  _timer=setInterval(async()=>{
    let s; try{ s=await (await fetch('/api/job/'+id)).json(); }catch(e){ return; }
    if(!s || !s.status){ clearInterval(_timer); box.style.display='none'; return; }
    if(s.status==='running'){
      box.innerHTML='⏳ '+s.stage+'  ·  단계 '+s.step+'/'+s.total+'  ·  경과 '+_fmt(s.elapsed);
    }else if(s.status==='done'){
      clearInterval(_timer); box.innerHTML='✅ 완료! 불러오는 중...'; location.reload();
    }else{
      clearInterval(_timer); box.innerHTML='⚠️ 오류: '+(s.error||'알 수 없음');
      document.querySelectorAll('button').forEach(b=>b.disabled=false);
    }
  },2000);
}
async function runJob(startUrl){
  _ensureBox().innerHTML='작업 시작 중...';
  document.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{ let j=await (await fetch(startUrl,{method:'POST'})).json(); pollJob(j.job_id); }
  catch(e){ _ensureBox().innerHTML='⚠️ 요청 실패'; document.querySelectorAll('button').forEach(b=>b.disabled=false); }
}
document.addEventListener('DOMContentLoaded',async function(){
  try{ let a=await (await fetch('/api/active')).json(); if(a && a.job_id){ pollJob(a.job_id); } }catch(e){}
});
</script>
"""

# 드래그 선택 → 그 자리에서 바로 질문 (탭 이동 없이 팝업으로 답변)
SELECT_ASK = """
<button id="selask">💬 이거 물어보기</button>
<div id="popask">
  <div class="ph">💬 선택 내용 물어보기 <span onclick="document.getElementById('popask').style.display='none'">✕</span></div>
  <div class="pb">
    <div class="q" id="pq"></div>
    <div id="pa" class="muted">브리핑에서 문장을 드래그한 뒤 버튼을 누르세요.</div>
    <div style="display:flex;gap:6px;margin-top:10px">
      <input id="pf" placeholder="추가 질문 (선택)" style="flex:1;padding:8px 10px;border:1px solid #cbd5e1;border-radius:8px;font-size:13px" onkeydown="if(event.key==='Enter')askSel()">
      <button onclick="askSel()" style="padding:8px 12px">전송</button>
    </div>
  </div>
</div>
<script>
let _sel='';
document.addEventListener('mouseup',function(e){
  if(e.target && e.target.id==='selask') return;
  let s=window.getSelection(); let txt=(s?s.toString():'').trim();
  let btn=document.getElementById('selask');
  let inCard = s.anchorNode && s.anchorNode.parentElement && s.anchorNode.parentElement.closest && s.anchorNode.parentElement.closest('.card');
  if(txt.length>0 && inCard){
    _sel=txt.slice(0,1500);
    let r=s.getRangeAt(0).getBoundingClientRect();
    btn.style.top=(window.scrollY+r.bottom+6)+'px';
    btn.style.left=(window.scrollX+r.left)+'px';
    btn.style.display='block';
  } else { btn.style.display='none'; }
});
document.addEventListener('mousedown',function(e){
  if(e.target && e.target.id!=='selask'){ document.getElementById('selask').style.display='none'; }
});
document.getElementById('selask').addEventListener('click',function(){
  this.style.display='none';
  document.getElementById('pq').innerText='\u201C'+_sel.slice(0,180)+(_sel.length>180?'\u2026':'')+'\u201D';
  document.getElementById('pf').value='';
  document.getElementById('popask').style.display='flex';
  askSel();
});
async function askSel(){
  let f=document.getElementById('pf').value.trim();
  let q='다음은 브리핑에서 선택한 내용입니다:\\n\\n"'+_sel+'"\\n\\n'+(f||'이 내용을 쉽게 설명하고, 투자 관점에서 핵심과 주의점을 짚어줘.');
  let pa=document.getElementById('pa'); pa.classList.remove('muted'); pa.innerText='생각 중...';
  try{
    let r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
    let j=await r.json(); pa.innerText=j.answer;
  }catch(e){ pa.innerText='오류가 발생했어요.'; }
}
</script>
"""


def page(title, active, body, side_html=""):
    nav = "".join(
        f'<a href="{href}" class="{"active" if active == key else ""}">{label}</a>'
        for key, href, label in [
            ("home", "/", "📊 대시보드"),
            ("ask", "/ask", "💬 물어보기"),
            ("outlook", "/outlook", "🔮 전망"),
        ])
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>{CSS}</style></head><body>
<header><h1>📈 데일리 투자 브리핑</h1><nav>{nav}</nav></header>
<div class="wrap"><div class="side">{side_html or "&nbsp;"}</div><div class="main">{body}</div></div>
{PROGRESS_JS}
{SELECT_ASK}
</body></html>"""


def archive_side():
    items = list_rows("briefings")
    links = "".join(f'<a href="/b/{r["id"]}">{r["date_label"]}</a>' for r in items) or \
        '<div class="muted" style="padding:8px">아직 없음</div>'
    return f"<h3>지난 브리핑</h3>{links}"


def outlook_side():
    items = list_rows("outlooks")
    links = "".join(f'<a href="/o/{r["id"]}">{r["date_label"]}</a>' for r in items) or \
        '<div class="muted" style="padding:8px">아직 없음</div>'
    return f"<h3>지난 전망</h3>{links}"


def render_md(content_md):
    return md.markdown(content_md, extensions=MD_EXT)


# ──────────────────────────── FastAPI ────────────────────────────
app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup():
    init_db()
    tz = ZoneInfo(TIMEZONE)
    sched = BackgroundScheduler(timezone=tz)
    h = int(os.environ.get("DAILY_HOUR", "6"))
    m = int(os.environ.get("DAILY_MINUTE", "13"))
    sched.add_job(run_daily_job, CronTrigger(hour=h, minute=m, timezone=tz),
                  id="daily", misfire_grace_time=3600)
    sched.add_job(run_outlook_job, CronTrigger(day=1, hour=h, minute=(m + 5) % 60, timezone=tz),
                  id="monthly", misfire_grace_time=7200)
    sched.start()
    print(f"[APP] 스케줄러 시작 — 매일 {h:02d}:{m:02d} ({TIMEZONE}), 매월 1일 전망")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(_: None = Depends(require_auth)):
    latest = latest_row("briefings")
    gen = ('<button onclick="runJob(\'/api/generate\')">🔄 지금 브리핑 생성</button>'
           '<div id="jobbox" class="jobbox"></div>')
    if not latest:
        body = ('<div class="card"><h1>아직 브리핑이 없어요</h1>'
                '<p>스케줄러가 다음 예약 시각에 첫 브리핑을 만들거나, 아래 버튼으로 지금 생성할 수 있어요. '
                '(웹서치 포함이라 1~2분 걸려요 — 진행 단계가 표시됩니다.)</p>'
                f'{gen}</div>')
        return page("대시보드", "home", body, archive_side())
    body = (f'<div class="card"><div style="display:flex;justify-content:space-between;align-items:center">'
            f'<div class="muted">생성: {latest["date_label"]}</div>'
            f'<button onclick="runJob(\'/api/generate\')" style="font-size:12px;padding:7px 12px">🔄 새로 생성</button>'
            f'</div><div id="jobbox" class="jobbox"></div>'
            f'{render_md(latest["content_md"])}</div>')
    return page("대시보드", "home", body, archive_side())


@app.get("/b/{rid}", response_class=HTMLResponse)
def view_briefing(rid: int, _: None = Depends(require_auth)):
    r = get_row("briefings", rid)
    if not r:
        raise HTTPException(404)
    body = (f'<div class="card"><div class="muted">생성: {r["date_label"]}</div>'
            f'{render_md(r["content_md"])}</div>')
    return page("브리핑", "home", body, archive_side())


@app.get("/outlook", response_class=HTMLResponse)
def outlook_page(_: None = Depends(require_auth)):
    latest = latest_row("outlooks")
    inner = render_md(latest["content_md"]) if latest else \
        "<h1>전망이 아직 없어요</h1><p>매월 1일 자동 생성되거나, 아래 버튼으로 지금 만들 수 있어요.</p>"
    head = f'<div class="muted">생성: {latest["date_label"]}</div>' if latest else ""
    body = (f'<div class="card">{head}{inner}'
            '<p style="margin-top:18px"><button onclick="runJob(\'/api/outlook\')">🔮 전망 새로 생성</button></p>'
            '<div id="jobbox" class="jobbox"></div></div>')
    return page("전망", "outlook", body, outlook_side())


@app.get("/o/{rid}", response_class=HTMLResponse)
def view_outlook(rid: int, _: None = Depends(require_auth)):
    r = get_row("outlooks", rid)
    if not r:
        raise HTTPException(404)
    body = (f'<div class="card"><div class="muted">생성: {r["date_label"]}</div>'
            f'{render_md(r["content_md"])}</div>')
    return page("전망", "outlook", body, outlook_side())


@app.get("/ask", response_class=HTMLResponse)
def ask_page(_: None = Depends(require_auth)):
    import html as _h
    hist = list_qas()
    hist_html = ""
    if hist:
        rows = "".join(
            f'<div style="border-top:1px solid #eef2f7;padding:12px 0">'
            f'<div class="muted">{r["date_label"]}</div>'
            f'<div style="font-weight:600;margin:4px 0">Q. {_h.escape(r["question"][:300])}</div>'
            f'<div style="font-size:13.5px;line-height:1.6">{render_md(r["answer"])}</div></div>'
            for r in hist)
        hist_html = f'<div class="card" style="margin-top:16px"><h2>🕘 지난 질문 기록</h2>{rows}</div>'
    body = ('<div class="card"><h1>💬 물어보기</h1>'
            '<p class="muted">최신 브리핑을 바탕으로, 필요하면 웹을 검색해 답합니다. (투자 자문 아님) · 모든 질문은 아래에 기록돼요.</p>'
            '<div id="log"></div>'
            '<div class="askbar"><input id="q" placeholder="예: 오늘 반도체 섹터 핵심만 요약해줘" '
            "onkeydown=\"if(event.key==='Enter')send()\"><button onclick=\"send()\">전송</button></div>"
            '<script>'
            'function add(t,cls){let d=document.createElement("div");d.className="msg "+cls;d.innerText=t;'
            'document.getElementById("log").appendChild(d);d.scrollIntoView();return d;}'
            'async function send(){let i=document.getElementById("q");let q=i.value.trim();if(!q)return;'
            'i.value="";add(q,"me");let a=add("생각 중...","ai");'
            'try{let r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},'
            'body:JSON.stringify({question:q})});let j=await r.json();a.innerText=j.answer;}'
            'catch(e){a.innerText="오류가 발생했어요.";}}'
            '</script></div>'
            + hist_html)
    return page("물어보기", "ask", body)


@app.post("/api/ask")
def api_ask(payload: dict, _: None = Depends(require_auth)):
    q = (payload or {}).get("question", "").strip()
    if not q:
        return JSONResponse({"answer": "질문을 입력해 주세요."})
    try:
        ans = answer_question(q)
        try:
            save_qa(q, ans)
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] Q&A 저장 실패: {e}")
        return JSONResponse({"answer": ans})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"answer": f"오류: {e}"}, status_code=500)


@app.post("/api/generate")
def api_generate(_: None = Depends(require_auth)):
    return {"job_id": start_job("daily", run_daily_job, 4)}


@app.post("/api/outlook")
def api_outlook(_: None = Depends(require_auth)):
    return {"job_id": start_job("outlook", run_outlook_job, 2)}


@app.get("/api/job/{jid}")
def api_job(jid: str, _: None = Depends(require_auth)):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404)
    return {"status": j["status"], "stage": j["stage"], "step": j["step"],
            "total": j["total"], "error": j["error"], "result_id": j["result_id"],
            "elapsed": int(time.time() - j["started"])}


@app.get("/api/active")
def api_active(_: None = Depends(require_auth)):
    """현재 실행 중인 작업이 있으면 그 id/종류 반환(페이지 이동 후 진행상황 복구용)."""
    running = [(jid, j) for jid, j in JOBS.items() if j["status"] == "running"]
    if not running:
        return {"job_id": None}
    running.sort(key=lambda kv: kv[1]["started"], reverse=True)
    jid, j = running[0]
    return {"job_id": jid, "kind": j["kind"],
            "stage": j["stage"], "step": j["step"], "total": j["total"],
            "elapsed": int(time.time() - j["started"])}
