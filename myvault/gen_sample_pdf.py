"""Generate a sample bank-statement-summary PDF for the desktop parsing demo."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "sample_bank_statement.pdf")

rows = [
    ["Month", "Salary Credit", "EMI Debits", "Avg Balance", "Bounces"],
    ["2026-01", "120000", "12000", "140000", "0"],
    ["2026-02", "119000", "12000", "151000", "0"],
    ["2026-03", "121000", "12000", "138000", "0"],
    ["2026-04", "120000", "12000", "162000", "0"],
]

styles = getSampleStyleSheet()
doc = SimpleDocTemplate(OUT, pagesize=A4, title="Bank Statement Summary")
elems = [
    Paragraph("MyVault Sample Bank — Statement Summary", styles["Title"]),
    Paragraph("Account holder: Ravi Kumar &nbsp;&nbsp; A/C: XXXX1234 &nbsp;&nbsp; Period: Jan–Apr 2026", styles["Normal"]),
    Spacer(1, 8 * mm),
]
t = Table(rows, colWidths=[30 * mm, 35 * mm, 30 * mm, 33 * mm, 22 * mm])
t.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222222")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#888888")),
    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ("FONTSIZE", (0, 0), (-1, -1), 10),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f3f3")]),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
]))
elems.append(t)
elems.append(Spacer(1, 8 * mm))
elems.append(Paragraph("This is a synthetic statement for demonstration only.", styles["Italic"]))
doc.build(elems)
print("wrote", OUT)
