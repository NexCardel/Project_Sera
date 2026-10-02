"""Two fictional clients on one portal-like page, built to shift keys. All data is made up."""
import json
from pathlib import Path

HERE = Path(__file__).parent

# Template texts: the same on both pages. Anything here reported "variable" is a pairing error.
LABELS = ["Dashboard", "Returns", "Payments", "Profile", "Taxpayer details", "GSTIN :", "Legal Name :",
          "Trade Name :", "Registration Date :", "Filed returns", "Period", "Form", "Status", "ARN",
          "Ledger balance", "IGST", "CGST", "SGST", "Cash ledger", "Credit ledger",
          "Designed by the test team", "Help desk"]

CLIENTS = {
    "A": {"gstin": "27AAAAA1111A1Z1", "legal": "ALPHA TRADERS", "trade": "ALPHA STORE", "reg": "01/04/2019",
          "notice": None,
          "returns": [("Jun 2026", "GSTR-1", "Filed", "AA2706261111111"),
                      ("May 2026", "GSTR-1", "Filed", "AA2705261111112"),
                      ("Apr 2026", "GSTR-1", "Filed", "AA2704261111113")],
          "cash": ("101", "202", "303"), "credit": ("1111", "2222", "3333")},
    "B": {"gstin": "29BBBBB2222B2Z2", "legal": "BETA FOODS", "trade": "BETA KITCHEN", "reg": "15/08/2021",
          "notice": "Your return for the quarter ending June is due on 13/07/2026.",
          "returns": [("Jun 2026", "GSTR-1", "Not filed", "-"),
                      ("Mar 2026", "GSTR-1", "Filed", "BB2903262222221"),
                      ("Dec 2025", "GSTR-1", "Filed", "BB2912252222222"),
                      ("Sep 2025", "GSTR-1", "Filed", "BB2909252222223"),
                      ("Jun 2025", "GSTR-1", "Filed", "BB2906252222224")],
          "cash": ("404", "505", "606"), "credit": ("4444", "5555", "6666")},
}


def page(name: str, c: dict) -> str:
    notice = f"<p>{c['notice']}</p>" if c["notice"] else ""
    rows = "".join(
        f'<div class="card"><div class="row"><span>Period</span><span>{p}</span></div>'
        f'<div class="row"><span>Form</span><span>{f}</span></div>'
        f'<div class="row"><span>Status</span><span>{s}</span></div>'
        f'<div class="row"><span>ARN</span><span>{a}</span></div></div>' for p, f, s, a in c["returns"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>SDIS align {name}</title></head><body>
<nav><a href="#">Dashboard</a> <a href="#">Returns</a> <a href="#">Payments</a> <a href="#">Profile</a></nav>
<div class="main">
<h2>Taxpayer details</h2>
{notice}
<div class="profile">
<span>GSTIN :</span><span>{c['gstin']}</span>
<span>Legal Name :</span><span>{c['legal']}</span>
<span>Trade Name :</span><span>{c['trade']}</span>
<span>Registration Date :</span><span>{c['reg']}</span>
</div>
<h3>Filed returns</h3>
<div class="list">{rows}</div>
<h3>Ledger balance</h3>
<table class="table"><tr><th></th><th>IGST</th><th>CGST</th><th>SGST</th></tr>
<tr><td>Cash ledger</td>{''.join(f'<td>{v}</td>' for v in c['cash'])}</tr>
<tr><td>Credit ledger</td>{''.join(f'<td>{v}</td>' for v in c['credit'])}</tr></table>
</div>
<footer><span>Designed by the test team</span> <a href="#">Help desk</a></footer>
</body></html>"""


for name, c in CLIENTS.items():
    (HERE / f"client_{name}.html").write_text(page(name, c), encoding="utf-8")
(HERE / "labels.json").write_text(json.dumps(LABELS), encoding="utf-8")
print("written")
