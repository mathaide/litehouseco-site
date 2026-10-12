#!/usr/bin/env python3
"""
MyVault - Lender Intelligence OS  |  Sovereign Demo
----------------------------------------------------
A fully self-contained, OFFLINE demonstration of the credit-decision wedge:
ingest borrower data -> deterministic policy engine -> explainable decision
-> maker-checker review -> audit trail.

No external network calls. No API keys. No data leaves this container.
Pure Python standard library only (no pip install required).
"""

import json
import os
import csv
import io
import datetime
import threading
import uuid
import socket
import base64
import webbrowser
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    import docparser
    DOCPARSER_OK = docparser.AVAILABLE
except Exception:
    DOCPARSER_OK = False

try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # __file__ is undefined if code is pasted into a console; fall back to cwd
    BASE_DIR = os.getcwd()
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(BASE_DIR, "data")
# Hosted mode (Railway): the audit trail goes to a writable folder, every request must
# carry the Litehouse gate key, and each visitor sees only their own audit entries.
STATE_DIR = os.environ.get("STATE_DIR", DATA_DIR)
AUDIT_FILE = os.path.join(STATE_DIR, "audit_log.jsonl")
os.environ.setdefault("GATE_HOSTS", "myvault.litehouseiq.com")
if os.environ.get("HOSTED"):
    from lhgate import GateVerifier
    GATE = GateVerifier(os.environ.get("GATE_PUBKEY_URL", "https://myvault.litehouseiq.com/__lh/pubkey"))
else:
    GATE = None
HOSTED = bool(os.environ.get("HOSTED"))
MAX_BODY = 12 * 1024 * 1024
_visitor = threading.local()

# ---- product-level credit policy (deterministic, explainable) ----
PRODUCTS = {
    "Home Loan":      {"rate": 0.090, "max_ltv": 0.80, "max_foir": 0.55, "min_bureau": 650},
    "Loan Against Property": {"rate": 0.105, "max_ltv": 0.65, "max_foir": 0.50, "min_bureau": 680},
    "Personal Loan":  {"rate": 0.160, "max_ltv": None, "max_foir": 0.45, "min_bureau": 700},
}

# Default applicant profile used when only a statement is parsed from a document.
DEFAULT_PROFILE = {
    "loan_product": "Home Loan",
    "requested_amount": 4500000,
    "tenure_months": 240,
    "declared_monthly_income": 120000,
    "bureau": {"score": 768, "active_loans": 1, "overdue_accounts": 0, "enquiries_last_6m": 2, "writeoffs": 0},
    "kyc": {"pan": True, "aadhaar": True, "address_proof": True},
    "collateral": {"property_value": 6500000},
}


def borrower_from_statement(stmt, name="Applicant (from PDF)"):
    b = dict(DEFAULT_PROFILE)
    b["id"] = "PDF"
    b["name"] = name
    b["bank_statement"] = stmt
    return b

_lock = threading.Lock()
DECISIONS = {}   # decision_id -> record


def now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def load_borrowers():
    with open(os.path.join(DATA_DIR, "borrowers.json"), "r") as f:
        return json.load(f)


def emi(principal, annual_rate, months):
    r = annual_rate / 12.0
    if r == 0:
        return principal / months
    factor = (1 + r) ** months
    return principal * r * factor / (factor - 1)


def derive_from_statement(stmt):
    """Compute income / obligations / red flags from a bank-statement object."""
    months = stmt.get("months", [])
    if not months:
        return {"observed_income": 0, "existing_emi": 0, "bounces": 0, "avg_balance": 0, "n_months": 0}
    salary = [m.get("salary_credit", 0) for m in months]
    emis = [m.get("emi_debits", 0) for m in months]
    bal = [m.get("avg_balance", 0) for m in months]
    bounces = sum(m.get("bounces", 0) for m in months)
    return {
        "observed_income": round(sum(salary) / len(salary)),
        "existing_emi": round(sum(emis) / len(emis)),
        "bounces": bounces,
        "avg_balance": round(sum(bal) / len(bal)),
        "n_months": len(months),
    }


def run_policy(borrower):
    """Core engine. Returns a structured, explainable decision."""
    product = borrower.get("loan_product", "Home Loan")
    policy = PRODUCTS.get(product, PRODUCTS["Home Loan"])
    amount = borrower.get("requested_amount", 0)
    tenure = borrower.get("tenure_months", 240)
    declared_income = borrower.get("declared_monthly_income", 0)
    bureau = borrower.get("bureau", {})
    kyc = borrower.get("kyc", {})
    collateral = borrower.get("collateral", {})

    s = derive_from_statement(borrower.get("bank_statement", {}))
    observed_income = s["observed_income"] or declared_income
    proposed_emi = emi(amount, policy["rate"], tenure) if amount else 0
    total_emi = s["existing_emi"] + proposed_emi
    foir = (total_emi / observed_income) if observed_income else 1.0

    ltv = None
    prop_value = collateral.get("property_value")
    if prop_value:
        ltv = amount / prop_value

    income_gap = 0.0
    if declared_income:
        income_gap = abs(observed_income - declared_income) / declared_income

    reasons = []        # adverse / attention reasons
    positives = []
    hard_fail = False
    soft_fail = False

    def add(level, text):
        nonlocal hard_fail, soft_fail
        reasons.append({"level": level, "text": text})
        if level == "FAIL":
            hard_fail = True
        elif level == "REFER":
            soft_fail = True

    # Bureau
    score = bureau.get("score")
    if score is None:
        add("REFER", "No bureau score available; manual bureau pull required.")
    elif score < policy["min_bureau"]:
        add("FAIL", f"Bureau score {score} is below product minimum {policy['min_bureau']}.")
    else:
        positives.append(f"Bureau score {score} meets the {policy['min_bureau']} minimum.")

    if bureau.get("overdue_accounts", 0) > 0:
        add("FAIL", f"{bureau['overdue_accounts']} account(s) currently overdue on the bureau report.")
    if bureau.get("writeoffs", 0) > 0:
        add("FAIL", f"{bureau['writeoffs']} written-off account(s) on record.")
    if bureau.get("enquiries_last_6m", 0) >= 6:
        add("REFER", f"High credit-hungry behaviour: {bureau['enquiries_last_6m']} enquiries in 6 months.")

    # Affordability
    if foir > policy["max_foir"]:
        add("FAIL", f"FOIR {foir:.0%} exceeds product ceiling {policy['max_foir']:.0%}.")
    elif foir > policy["max_foir"] - 0.10:
        add("REFER", f"FOIR {foir:.0%} is close to the ceiling {policy['max_foir']:.0%}; thin affordability.")
    else:
        positives.append(f"FOIR {foir:.0%} is within the {policy['max_foir']:.0%} ceiling.")

    # Collateral / LTV
    if policy["max_ltv"] is not None:
        if ltv is None:
            add("REFER", "Collateral valuation missing; LTV cannot be computed.")
        elif ltv > policy["max_ltv"]:
            add("FAIL", f"LTV {ltv:.0%} exceeds product cap {policy['max_ltv']:.0%}.")
        else:
            positives.append(f"LTV {ltv:.0%} is within the {policy['max_ltv']:.0%} cap.")

    # Income consistency
    if income_gap > 0.25:
        add("REFER", f"Declared income differs from bank-statement income by {income_gap:.0%}.")
    elif declared_income:
        positives.append(f"Declared vs observed income consistent (gap {income_gap:.0%}).")

    # Banking conduct
    if s["bounces"] >= 3:
        add("REFER", f"{s['bounces']} cheque/ECS bounces in the statement period.")
    elif s["n_months"]:
        positives.append(f"Clean banking conduct ({s['bounces']} bounces over {s['n_months']} months).")

    # KYC / document defects
    missing = [k for k in ("pan", "aadhaar", "address_proof") if not kyc.get(k, False)]
    if missing:
        add("REFER", "Document defect: missing " + ", ".join(m.replace("_", " ") for m in missing) + ".")
    else:
        positives.append("KYC documents complete (PAN, Aadhaar, address proof).")

    if hard_fail:
        recommendation = "DECLINE"
    elif soft_fail:
        recommendation = "REFER"
    else:
        recommendation = "APPROVE"

    return {
        "recommendation": recommendation,
        "metrics": {
            "product": product,
            "observed_income": observed_income,
            "declared_income": declared_income,
            "existing_emi": s["existing_emi"],
            "proposed_emi": round(proposed_emi),
            "foir": round(foir, 4),
            "ltv": round(ltv, 4) if ltv is not None else None,
            "bureau_score": score,
            "bounces": s["bounces"],
            "statement_months": s["n_months"],
            "interest_rate": policy["rate"],
        },
        "reasons": reasons,
        "positives": positives,
        "policy": policy,
    }


def parse_statement_csv(text):
    """Parse an uploaded bank-statement CSV into the months structure.
    Expected columns: month, salary_credit, emi_debits, avg_balance, bounces
    """
    months = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        def g(k):
            try:
                return float(str(row.get(k, "0")).replace(",", "").strip() or 0)
            except ValueError:
                return 0
        months.append({
            "month": row.get("month", "").strip(),
            "salary_credit": g("salary_credit"),
            "emi_debits": g("emi_debits"),
            "avg_balance": g("avg_balance"),
            "bounces": int(g("bounces")),
        })
    return {"months": months}


def write_audit(entry):
    entry = dict(entry)
    entry["ts"] = now_iso()
    sid = getattr(_visitor, "sid", None)
    with _lock:
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            if sid:
                entry = dict(entry, sid=sid)
            with open(AUDIT_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
    entry.pop("sid", None)
    return entry


def read_audit():
    out = []
    if os.path.exists(AUDIT_FILE):
        with open(AUDIT_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    sid = getattr(_visitor, "sid", None)
    if HOSTED:
        out = [e for e in out if e.get("sid") == sid]
    for e in out:
        e.pop("sid", None)
    return out[-100:]


class Handler(BaseHTTPRequestHandler):
    server_version = "MyVault/0.1"

    def log_message(self, *a):
        pass  # quiet

    def _guard(self):
        """Hosted mode: refuse anyone who did not come through the Litehouse gate,
        and tag the visitor with a cookie so their audit trail is their own."""
        if urlparse(self.path).path == "/healthz":
            self._send(200, {"ok": True})
            return False
        if GATE is not None and not GATE.check(self.command, self.path, self.headers):
            self._send(404, {"error": "not found"})
            return False
        sid = None
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "mv_sid" and v.isalnum() and len(v) <= 40:
                sid = v
        self._new_sid = None
        if not sid:
            sid = secrets.token_hex(12)
            self._new_sid = sid
        _visitor.sid = sid
        return True

    def end_headers(self):
        if getattr(self, "_new_sid", None):
            self.send_header("Set-Cookie", f"mv_sid={self._new_sid}; Path=/; Max-Age=604800; HttpOnly; Secure; SameSite=Lax")
            self._new_sid = None
        if HOSTED:
            self.send_header("X-Robots-Tag", "noindex")
        super().end_headers()

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode())
        except Exception:
            return {}

    def _serve_static(self, path):
        if path in ("/", "", "/index.html"):
            path = "/index.html"
        fpath = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not fpath.startswith(STATIC_DIR) or not os.path.isfile(fpath):
            return self._send(404, {"error": "not found"})
        ctype = "text/html"
        if fpath.endswith(".css"):
            ctype = "text/css"
        elif fpath.endswith(".js"):
            ctype = "application/javascript"
        elif fpath.endswith(".csv"):
            ctype = "text/csv"
        with open(fpath, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        if not self._guard():
            return
        p = urlparse(self.path).path
        if p == "/api/health":
            return self._send(200, {"status": "ok", "sovereign": True, "offline": True})
        if p == "/api/borrowers":
            return self._send(200, load_borrowers())
        if p == "/api/audit":
            return self._send(200, read_audit())
        if p == "/api/sample-statement":
            fpath = os.path.join(DATA_DIR, "sample_bank_statement.csv")
            with open(fpath, "rb") as f:
                return self._send(200, f.read(), "text/csv")
        if p == "/api/parse-sample-pdf":
            if not DOCPARSER_OK:
                return self._send(400, {"error": "pdfplumber not installed; run: pip install pdfplumber"})
            fpath = os.path.join(DATA_DIR, "sample_bank_statement.pdf")
            if not os.path.exists(fpath):
                return self._send(404, {"error": "sample PDF missing"})
            with open(fpath, "rb") as f:
                stmt = docparser.parse_pdf_bytes(f.read())
            return self._send(200, {"bank_statement": stmt, "derived": derive_from_statement(stmt),
                                    "borrower": borrower_from_statement(stmt, "Ravi Kumar (from PDF)")})
        return self._serve_static(p)

    def do_POST(self):
        if not self._guard():
            return
        p = urlparse(self.path).path
        body = self._read_json()

        if p == "/api/parse-statement":
            text = body.get("csv_text", "")
            try:
                stmt = parse_statement_csv(text)
                return self._send(200, {"bank_statement": stmt, "derived": derive_from_statement(stmt)})
            except Exception as e:
                return self._send(400, {"error": f"could not parse CSV: {e}"})

        if p == "/api/parse-pdf":
            if not DOCPARSER_OK:
                return self._send(400, {"error": "pdfplumber not installed; run: pip install pdfplumber"})
            b64 = body.get("pdf_base64", "")
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            try:
                raw = base64.b64decode(b64)
                stmt = docparser.parse_pdf_bytes(raw)
                if not stmt.get("months"):
                    return self._send(400, {"error": "no statement table found in this PDF"})
                return self._send(200, {"bank_statement": stmt, "derived": derive_from_statement(stmt),
                                        "borrower": borrower_from_statement(stmt)})
            except Exception as e:
                return self._send(400, {"error": f"could not parse PDF: {e}"})

        if p == "/api/decide":
            borrower = body.get("borrower")
            if not borrower:
                bid = body.get("borrower_id")
                borrower = next((b for b in load_borrowers() if b["id"] == bid), None)
            if not borrower:
                return self._send(400, {"error": "borrower not provided"})
            result = run_policy(borrower)
            did = uuid.uuid4().hex[:8]
            rec = {
                "decision_id": did,
                "borrower_id": borrower.get("id"),
                "borrower_name": borrower.get("name"),
                "result": result,
                "status": "PENDING_REVIEW",
                "maker": "MyVault Analyst Agent",
            }
            with _lock:
                DECISIONS[did] = dict(rec, _sid=getattr(_visitor, "sid", None))
                if len(DECISIONS) > 5000:
                    for k in list(DECISIONS)[:1000]:
                        DECISIONS.pop(k, None)
            write_audit({
                "event": "DECISION_GENERATED", "decision_id": did,
                "borrower": borrower.get("name"), "actor": "MyVault Analyst Agent",
                "recommendation": result["recommendation"],
            })
            return self._send(200, rec)

        if p == "/api/review":
            did = body.get("decision_id")
            action = body.get("action")           # APPROVE / DECLINE / OVERRIDE
            checker = body.get("checker", "Credit Officer")
            reason = body.get("reason", "").strip()
            rec = DECISIONS.get(did)
            if rec and HOSTED and rec.get("_sid") != getattr(_visitor, "sid", None):
                rec = None
            if not rec:
                return self._send(404, {"error": "unknown decision"})
            if action == "OVERRIDE" and not reason:
                return self._send(400, {"error": "override requires a reason (maker-checker control)"})
            rec["status"] = "REVIEWED"
            rec["checker"] = checker
            rec["checker_action"] = action
            rec["checker_reason"] = reason
            entry = write_audit({
                "event": "MAKER_CHECKER_REVIEW", "decision_id": did,
                "borrower": rec.get("borrower_name"), "actor": checker,
                "action": action, "reason": reason,
            })
            return self._send(200, {"ok": True, "record": {k: v for k, v in rec.items() if k != "_sid"}, "audit": entry})

        return self._send(404, {"error": "not found"})


def find_free_port(preferred):
    """Return the preferred port if free, otherwise the next available one.
    Avoids the 'Address already in use' error when re-running in an IDE."""
    for p in [preferred, 8081, 8082, 8090, 0]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", p))
            chosen = s.getsockname()[1]
            s.close()
            return chosen
        except OSError:
            continue
    return preferred


def main():
    preferred = int(os.environ.get("PORT", "8080"))
    port = preferred if HOSTED else find_free_port(preferred)
    os.makedirs(STATE_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    url = f"http://localhost:{port}"
    print("=" * 64)
    print("  MyVault - Lender Intelligence OS  (sovereign demo)")
    print(f"  OPEN THIS IN YOUR BROWSER:  {url}")
    print("  Offline - no data leaves this machine.")
    print("  To STOP: click the red Stop square in Spyder, or press Ctrl+C.")
    print("=" * 64)

    # Try to open the browser automatically; harmless if it can't.
    def _open_browser():
        import time
        time.sleep(1.0)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    if not HOSTED:
        threading.Thread(target=_open_browser, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
        print("\nStopped.")


if __name__ == "__main__":
    main()
