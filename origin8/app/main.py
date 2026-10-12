"""Origin8 Revenue OS: Python backend.

Serves the Origin8 web app and its /api endpoints. No third-party AI or hosting
service is required: lead scoring, ROI projection, lender matching and the
Copilot are rule-based and run inside this process. Data is kept in SQLite.

Environment:
  GATE_PUBKEY_URL  public key of the Litehouse access gate. Every request must
               carry the gate's signature, so the service cannot be reached around
               the access-code gate. Use "off" only when running locally.
  DATA_DIR     folder for the SQLite file (default ./var). Mount a volume here.
  PORT         listening port (default 8080).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
import sys  # noqa: E402
sys.path.insert(0, str(HERE))
WEB = HERE.parent / "web"
SEED = json.loads((HERE / "seed.json").read_text(encoding="utf-8"))
DATA_DIR = Path(os.environ.get("DATA_DIR", HERE.parent / "var"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "origin8.db"
os.environ.setdefault("GATE_HOSTS", "origin8.litehouseiq.com")
from lhgate import GateVerifier  # noqa: E402
GATE = GateVerifier(os.environ.get("GATE_PUBKEY_URL", "https://origin8.litehouseiq.com/__lh/pubkey"))

AGENTS = SEED["agents"]
CATEGORIES = SEED["categories"]
AGENT_NAMES = [a["name"] for a in AGENTS]

app = FastAPI(title="Origin8 Revenue OS", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- utilities
def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def day_rng(*parts: Any) -> random.Random:
    """Random numbers that stay stable within a day, so pages do not jump on reload."""
    key = now().strftime("%Y-%m-%d") + ":" + ":".join(str(p) for p in parts)
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))


_lock = threading.Lock()


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _lock, db() as con:
        con.execute("create table if not exists leads (id text primary key, created_at text, doc text)")
        con.execute("create table if not exists contacts (id text primary key, created_at text, doc text)")
        con.execute("create table if not exists chats (id integer primary key autoincrement, session_id text, role text, content text, created_at text)")
        if con.execute("select count(*) from leads").fetchone()[0] == 0:
            # Seed leads keep their relative spacing but are moved to end yesterday.
            leads = SEED["leads"]
            latest = max(datetime.fromisoformat(l["created_at"]) for l in leads)
            shift = (now() - timedelta(days=1)) - latest
            for l in leads:
                l = json.loads(json.dumps(l))
                for k in ("created_at", "last_activity"):
                    l[k] = iso(datetime.fromisoformat(l[k]) + shift)
                for t in l.get("timeline", []):
                    t["ts"] = iso(datetime.fromisoformat(t["ts"]) + shift)
                con.execute("insert into leads values (?,?,?)", (l["id"], l["created_at"], json.dumps(l)))


def all_leads(limit: int = 500) -> list[dict]:
    with db() as con:
        rows = con.execute("select doc from leads order by created_at desc limit ?", (limit,)).fetchall()
    return [json.loads(r["doc"]) for r in rows]


# ------------------------------------------------------------ gate check
@app.middleware("http")
async def gate_key(request: Request, call_next):
    if request.url.path != "/healthz":
        raw = request.scope.get("raw_path") or request.url.path.encode()
        qs = request.scope.get("query_string") or b""
        path_qs = raw.decode("latin-1") + ("?" + qs.decode("latin-1") if qs else "")
        if not GATE.check(request.method, path_qs, request.headers):
            return PlainTextResponse("Not found", status_code=404)
    resp = await call_next(request)
    resp.headers["x-robots-tag"] = "noindex"
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------------------------------------ read APIs
@app.get("/api/")
def root():
    return {"service": "Origin8 Revenue OS", "version": "2.0.0", "status": "ok"}


def agent_metrics(a: dict) -> dict:
    r = day_rng("agent", a["name"])
    status = a.get("status", "active")
    return {**a,
            "uptime_pct": round(r.uniform(98.6, 99.99), 2) if status == "active" else round(r.uniform(95, 98.5), 2),
            "actions_24h": r.randint(900, 9800) if status == "active" else r.randint(0, 400),
            "success_rate": round(r.uniform(0.82, 0.95), 3)}


@app.get("/api/agents")
def agents(category: Optional[str] = None):
    items = [agent_metrics(a) for a in AGENTS if not category or a["category"] == category]
    return {"categories": CATEGORIES, "agents": items}


@app.get("/api/agents/health")
def agents_health():
    items = [agent_metrics(a) for a in AGENTS]
    active = sum(1 for a in items if a["status"] == "active")
    return {"summary": {"total": len(items), "active": active, "idle": len(items) - active,
                        "actions_24h": sum(a["actions_24h"] for a in items),
                        "avg_success_rate": round(sum(a["success_rate"] for a in items) / len(items), 3)},
            "agents": items}


@app.get("/api/integrations")
def integrations():
    return SEED["integrations"]


@app.get("/api/journeys")
def journeys():
    return SEED["journeys"]


@app.get("/api/industries/{slug}")
def industry(slug: str):
    item = SEED["industries"].get(slug)
    if not item:
        raise HTTPException(404, "Industry not found")
    return item


@app.get("/api/leads")
def leads(limit: int = 200):
    return all_leads(min(max(limit, 1), 500))


@app.get("/api/analytics/summary")
def summary():
    ls = all_leads()
    health = agents_health()["summary"]
    r = day_rng("summary")
    converted = sum(1 for l in ls if l["status"] == "converted")
    return {"total_leads": len(ls),
            "hot_leads": sum(1 for l in ls if l["score_tier"] == "hot"),
            "converted": converted,
            "avg_score": round(sum(l["score"] for l in ls) / max(len(ls), 1), 1),
            "revenue_30d": 1_150_000 + converted * 9_500 + r.randint(0, 120_000),
            "cac_30d": r.randint(120, 160),
            "active_agents": health["active"],
            "actions_24h": health["actions_24h"]}


@app.get("/api/analytics/timeseries")
def timeseries():
    out = []
    today = now().date()
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        r = random.Random(d.toordinal())
        leads_n = r.randint(600, 820) + (29 - i) * 4
        conv = round(r.uniform(0.17, 0.2) + (29 - i) * 0.0015, 3)
        appr = int(leads_n * conv)
        out.append({"date": d.strftime("%b %d").replace(" 0", " "), "cac": r.randint(200, 235) - (29 - i),
                    "conversion": conv, "approvals": appr,
                    "predicted_approvals": int(appr * r.uniform(1.08, 1.16)), "leads": leads_n})
    return out


@app.get("/api/dashboard/overview")
def overview():
    ls = all_leads(8)
    t = now()
    alerts = [{**a, "ts": iso(t - timedelta(hours=h))} for a, h in zip(SEED["alerts"], (1, 3, 6, 10))]
    ribbon_names = ["SignalHunter", "EnrichmentEngine", "ScoreSmith", "EmailMaestro", "LenderMatcher", "LoginShepherd", "DriftDetector"]
    r = day_rng("ribbon")
    ribbon = [{"name": n, "status": "idle" if n == "DriftDetector" else "active",
               "actions": r.randint(2, 9) if n == "DriftDetector" else r.randint(35, 95)} for n in ribbon_names]
    return {"recent_leads": ls, "alerts": alerts, "agent_ribbon": ribbon}


# ------------------------------------------------------------ lead scoring
INDUSTRY_FIT = {"mortgages": 26, "financial": 24, "lending": 24, "cards": 20, "insurance": 18, "medical": 14}
INTENT_WORDS = {
    "urgent": 8, "this quarter": 8, "this month": 8, "asap": 8, "immediately": 8,
    "mortgage": 6, "loan": 6, "origination": 6, "underwrit": 6, "kyc": 5, "approval": 5,
    "lead": 4, "conversion": 4, "automate": 4, "funnel": 4, "whatsapp": 3, "email": 2,
    "pilot": 5, "budget": 5, "enterprise": 5, "scale": 4,
}
AGENT_FOR = {"mortgages": "LenderMatcher", "lending": "LenderMatcher", "financial": "LenderMatcher",
             "cards": "LoginShepherd", "insurance": "EmailMaestro", "medical": "VoiceConcierge"}


class LeadIn(BaseModel):
    name: str
    email: str
    company: Optional[str] = ""
    industry: Optional[str] = "financial"
    notes: Optional[str] = ""
    phone: Optional[str] = None
    source: Optional[str] = "Demo Form"


def score_lead(l: LeadIn) -> tuple[int, str, str]:
    text = f"{l.notes or ''} {l.company or ''}".lower()
    fit = INDUSTRY_FIT.get((l.industry or "").lower(), 12)
    hits = [w for w in INTENT_WORDS if w in text]
    intent = min(sum(INTENT_WORDS[w] for w in hits), 34)
    volume = 0
    m = re.search(r"(\d[\d,]*)\s*(k|thousand)?\s*(mortgages|loans|leads|applications|customers|policies|cards)", text)
    if m:
        n = int(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        volume = 14 if n >= 1000 else 11 if n >= 100 else 6
    domain = l.email.split("@")[-1].lower()
    corporate = 0 if domain in {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com"} else 8
    company = 6 if (l.company or "").strip() else 0
    score = max(5, min(98, 18 + fit + intent + volume + corporate + company))
    tier = "hot" if score >= 75 else "warm" if score >= 50 else "cold"
    parts = [f"{(l.industry or 'general').title()} fit"]
    if hits:
        parts.append("intent signals: " + ", ".join(hits[:4]))
    if volume:
        parts.append("stated volume " + m.group(0))
    parts.append("corporate email" if corporate else "personal email, verify company")
    reason = "; ".join(parts) + f". Scored {score} by the Origin8 rules engine."
    return score, tier, reason


@app.post("/api/leads")
def create_lead(body: LeadIn):
    if "@" not in body.email:
        raise HTTPException(422, "A valid email is required.")
    score, tier, reason = score_lead(body)
    t = now()
    agent = AGENT_FOR.get((body.industry or "").lower(), "ScoreSmith")
    lead = {"id": str(uuid.uuid4()), "name": body.name.strip()[:120], "email": body.email.strip()[:200],
            "phone": body.phone, "company": (body.company or "").strip()[:160], "industry": body.industry,
            "source": body.source or "Demo Form", "status": "new", "score": score, "score_tier": tier,
            "score_reasoning": reason, "assigned_agent": agent, "created_at": iso(t), "last_activity": iso(t),
            "notes": (body.notes or "")[:2000],
            "timeline": [{"ts": iso(t), "event": "Lead captured via demo form", "agent": "SignalHunter"},
                         {"ts": iso(t), "event": f"Scored: {score} ({tier})", "agent": "ScoreSmith"},
                         {"ts": iso(t), "event": f"Routed to {agent}", "agent": "ChannelOrchestrator"}]}
    with _lock, db() as con:
        con.execute("insert into leads values (?,?,?)", (lead["id"], lead["created_at"], json.dumps(lead)))
    return lead


# ------------------------------------------------------------ ROI
class RoiIn(BaseModel):
    monthly_leads: float = 5000
    current_conversion: float = 0.08
    avg_deal_value: float = 1800
    industry: Optional[str] = "financial"


UPLIFT = {"mortgages": 0.42, "financial": 0.38, "lending": 0.38, "cards": 0.35, "insurance": 0.31, "medical": 0.28}


@app.post("/api/roi")
def roi(body: RoiIn):
    leads_n = max(body.monthly_leads, 0)
    conv = min(max(body.current_conversion, 0), 1)
    value = max(body.avg_deal_value, 0)
    lift = UPLIFT.get((body.industry or "").lower(), 0.3)
    new_conv = min(conv * (1 + lift), 0.95)
    monthly = round(leads_n * (new_conv - conv) * value)
    extra_deals = round(leads_n * (new_conv - conv))
    insights = (f"At {leads_n:,.0f} leads a month and {conv:.0%} conversion, Origin8 projects conversion of "
                f"{new_conv:.1%}, about {extra_deals:,} extra closed deals a month. Most of the lift comes from "
                f"faster first response (SignalHunter and EmailMaestro) and lender matching on qualified leads. "
                f"The projection assumes a {lift:.0%} relative uplift typical for {(body.industry or 'financial')} pilots.")
    return {"monthly_uplift": monthly, "annual_uplift": monthly * 12, "new_conversion": round(new_conv, 4),
            "extra_deals_per_month": extra_deals, "insights": insights}


# ------------------------------------------------------------ lender match
class MatchIn(BaseModel):
    loan_type: str = "mortgage"
    amount: float = 400000
    credit_score: float = 720
    income: float = 110000


LENDERS = [
    {"lender": "Cedar Mortgage", "types": {"mortgage"}, "base": 6.1, "min_score": 640, "speed": 48},
    {"lender": "Northwind Lending", "types": {"mortgage", "personal", "sme"}, "base": 7.4, "min_score": 600, "speed": 24},
    {"lender": "Lumen Finance", "types": {"personal", "card"}, "base": 9.9, "min_score": 620, "speed": 2},
    {"lender": "Ridge Loans", "types": {"personal", "sme"}, "base": 11.2, "min_score": 580, "speed": 6},
    {"lender": "Aegis Capital", "types": {"sme", "mortgage"}, "base": 8.3, "min_score": 660, "speed": 72},
    {"lender": "Orbit Cards", "types": {"card"}, "base": 18.9, "min_score": 600, "speed": 1},
    {"lender": "Beacon Credit Union", "types": {"mortgage", "personal", "card"}, "base": 6.8, "min_score": 680, "speed": 36},
]


@app.post("/api/lender-match")
def lender_match(body: MatchIn):
    lt = body.loan_type.lower()
    dti = body.amount / max(body.income, 1)
    out = []
    for l in LENDERS:
        if lt not in l["types"]:
            continue
        gap = body.credit_score - l["min_score"]
        likely = 0.35 + gap / 300 - max(dti - (4.5 if lt == "mortgage" else 0.6), 0) * 0.15
        likely = round(min(max(likely, 0.05), 0.97), 2)
        rate = round(l["base"] + max(0, 740 - body.credit_score) / 40 * 0.35, 2)
        note = "Prime tier" if body.credit_score >= 740 else "Near prime" if body.credit_score >= 670 else "Manual review"
        if dti > (5 if lt == "mortgage" else 0.8):
            note += " · high amount to income"
        out.append({"lender": l["lender"], "rate_apr": rate, "approval_likelihood": likely,
                    "decision_speed_hours": l["speed"], "notes": note})
    out.sort(key=lambda x: (-x["approval_likelihood"], x["rate_apr"]))
    return out[:5]


# ------------------------------------------------------------ contact
class ContactIn(BaseModel):
    name: str
    email: str
    company: Optional[str] = ""
    message: Optional[str] = ""


@app.post("/api/contact")
def contact(body: ContactIn):
    doc = {"id": str(uuid.uuid4()), **body.model_dump(), "created_at": iso(now())}
    with _lock, db() as con:
        con.execute("insert into contacts values (?,?,?)", (doc["id"], doc["created_at"], json.dumps(doc)))
    return {"ok": True, "id": doc["id"], "routed_to": "customer@litehouseco.com"}


# ------------------------------------------------------------ payments (disabled)
class CheckoutIn(BaseModel):
    package_id: str
    origin_url: Optional[str] = ""


@app.post("/api/payments/checkout")
def checkout(body: CheckoutIn):
    # Online payment is not offered on the demonstration. Plans go through sales.
    return {"url": "/contact", "session_id": None}


@app.get("/api/payments/checkout/status/{session_id}")
def checkout_status(session_id: str):
    return {"status": "expired", "payment_status": "unpaid"}


# ------------------------------------------------------------ copilot
class ChatIn(BaseModel):
    session_id: str
    message: str


def copilot_reply(msg: str) -> str:
    m = msg.lower()
    ls = all_leads()
    if any(w in m for w in ("prioriti", "priority", "today", "which leads", "hot")):
        top = sorted([l for l in ls if l["status"] not in ("converted",)], key=lambda l: -l["score"])[:3]
        lines = [f"{l['name']} at {l['company']} (score {l['score']}, {l['status']}, with {l['assigned_agent']})" for l in top]
        return ("Start with these three: " + "; ".join(lines) +
                f". All have scores of {min(l['score'] for l in top)} or more and have not converted yet.")
    if any(w in m for w in ("trend", "week", "conversion", "summar")):
        ts = timeseries()
        last, prev = ts[-7:], ts[-14:-7]
        c1 = sum(x["conversion"] for x in last) / 7
        c0 = sum(x["conversion"] for x in prev) / 7
        a1 = sum(x["approvals"] for x in last)
        return (f"This week conversion averaged {c1:.1%}, against {c0:.1%} the week before. "
                f"Approvals totalled {a1:,}. CAC continued to ease as ScoreSmith filtered low intent leads earlier.")
    if "whatsapp" in m:
        return ("WhatsApp underperformed because one message template is waiting for re-approval, so "
                "WhatsAppCadence fell back to email for part of the week. Re-approving the template should restore it.")
    if any(w in m for w in ("cadence", "draft", "follow")):
        return ("Suggested cadence for hot mortgage leads: Day 0, call within 5 minutes (VoiceConcierge) and send the "
                "rate summary by email. Day 1, WhatsApp with document checklist. Day 3, LenderMatcher sends two "
                "pre-approved options. Day 5, human advisor call. Day 7, final nudge with the rate lock deadline.")
    s = summary()
    return (f"Right now there are {s['total_leads']} leads, {s['hot_leads']} of them hot, and {s['active_agents']} agents "
            f"active. Ask me which leads to prioritise, how conversion is trending, or to draft a cadence.")


@app.post("/api/copilot/chat")
def copilot(body: ChatIn):
    reply = copilot_reply(body.message[:2000])
    with _lock, db() as con:
        con.execute("insert into chats (session_id, role, content, created_at) values (?,?,?,?)",
                    (body.session_id[:40], "user", body.message[:2000], iso(now())))
        con.execute("insert into chats (session_id, role, content, created_at) values (?,?,?,?)",
                    (body.session_id[:40], "assistant", reply, iso(now())))
    return {"reply": reply, "session_id": body.session_id}


# ------------------------------------------------------------ web app
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")


@app.get("/robots.txt")
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_404(rest: str):
    return JSONResponse({"detail": "Not found"}, status_code=404)


@app.get("/{rest:path}")
def spa(rest: str):
    return FileResponse(WEB / "index.html", headers={"cache-control": "no-store"})


init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
