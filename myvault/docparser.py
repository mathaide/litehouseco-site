"""
MyVault desktop document parser.
Turns a bank-statement PDF into the structured 'months' format the policy engine uses.
Runs locally on a laptop/desktop. Requires: pip install pdfplumber  (no network at runtime).
"""
import io

try:
    import pdfplumber
    AVAILABLE = True
except Exception:
    AVAILABLE = False

# header label -> canonical field
_HEADERS = {
    "month": "month",
    "salary credit": "salary_credit", "salary_credit": "salary_credit", "credit": "salary_credit",
    "emi debits": "emi_debits", "emi_debits": "emi_debits", "emi": "emi_debits",
    "avg balance": "avg_balance", "avg_balance": "avg_balance", "balance": "avg_balance",
    "bounces": "bounces",
}


def _num(s):
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0


def parse_pdf_bytes(data):
    """Return {'months':[...], 'source':'pdf'} extracted from a bank-statement PDF."""
    if not AVAILABLE:
        raise RuntimeError("pdfplumber not installed - run: pip install pdfplumber")
    months = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                if not table or len(table) < 2:
                    continue
                header = [(h or "").strip().lower() for h in table[0]]
                idx = {}
                for i, h in enumerate(header):
                    key = _HEADERS.get(h)
                    if key:
                        idx[key] = i
                if "month" in idx and "salary_credit" in idx:
                    for row in table[1:]:
                        if not row or all(c is None or str(c).strip() == "" for c in row):
                            continue
                        months.append({
                            "month": str(row[idx["month"]]).strip() if idx.get("month") is not None else "",
                            "salary_credit": _num(row[idx["salary_credit"]]) if "salary_credit" in idx else 0,
                            "emi_debits": _num(row[idx["emi_debits"]]) if "emi_debits" in idx else 0,
                            "avg_balance": _num(row[idx["avg_balance"]]) if "avg_balance" in idx else 0,
                            "bounces": int(_num(row[idx["bounces"]])) if "bounces" in idx else 0,
                        })
                    if months:
                        return {"months": months, "source": "pdf"}
    return {"months": months, "source": "pdf"}
