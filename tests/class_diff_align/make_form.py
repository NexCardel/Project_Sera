"""A fictional form page for tools/browser_parity.py. All data is made up.

  python make_form.py  -> form.html beside this file (title 'SDIS parity form')

The password box holds 'Fictional#123': no reader may ever return it (a password field's content
is never read), and browser_parity.py checks exactly that in every browser.
"""
from pathlib import Path

HERE = Path(__file__).parent
TITLE = "SDIS parity form"
PASSWORD = "Fictional#123"

PAGE = f"""<!doctype html><html><head><meta charset="utf-8"><title>{TITLE}</title></head><body>
<h2>Return details</h2>
<form>
<label for="period">Return period</label>
<select id="period"><option>April</option><option selected>May</option><option>June</option></select>
<fieldset><legend>Filing frequency</legend>
<label><input type="radio" name="freq" value="m"> Monthly</label>
<label><input type="radio" name="freq" value="q" checked> Quarterly</label>
<label><input type="radio" name="freq" value="y"> Yearly</label>
</fieldset>
<label><input type="checkbox" id="nil" checked> Nil return</label>
<label for="trade">Trade name</label>
<input type="text" id="trade" value="GAMMA SUPPLIES">
<label for="pwd">Password</label>
<input type="password" id="pwd" value="{PASSWORD}">
</form>
<h3>Summary</h3>
<table><tr><th>Tax</th><th>Amount</th></tr>
<tr><td>IGST</td><td>707</td></tr>
<tr><td>CGST</td><td>808</td></tr></table>
</body></html>"""


def write(folder: Path = HERE) -> Path:
    path = Path(folder) / "form.html"
    path.write_text(PAGE, encoding="utf-8")
    return path


if __name__ == "__main__":
    print(f"written {write()}")
