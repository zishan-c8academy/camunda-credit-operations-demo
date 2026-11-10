"""Render the consumer evidence documents (PDF plus a PNG thumbnail) for each dispute scenario.

  /usr/bin/python3 scripts/make_documents.py      (needs Playwright; writes data/documents/)

Each document carries exactly the facts in data/systems.json consumerEvidence, so the file a person opens agrees with
what the investigator agent reports. Organisations and people are fictional. The server uploads these to Camunda's
document store when a dispute starts (variable evidenceDocuments).
"""
import json, os
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "documents")

CSS = """*{box-sizing:border-box;margin:0;padding:0}body{font:13px/1.45 Helvetica,Arial,sans-serif;color:#222;background:#fff}
.page{width:794px;min-height:1123px;padding:56px 64px;position:relative}
.hd{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid var(--c);padding-bottom:16px;margin-bottom:26px}
.org{font:700 22px/1.1 Georgia,serif;color:var(--c)}.org small{display:block;font:11px Helvetica,Arial;color:#666;margin-top:6px;letter-spacing:.5px}
.ttl{font-size:12px;text-align:right;color:#555}.ttl b{display:block;font-size:17px;color:#222;margin-bottom:4px}
h2{font-size:15px;margin:22px 0 10px}p{margin:0 0 10px}
table{width:100%;border-collapse:collapse;margin-top:6px}th{text-align:left;font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:#666;border-bottom:1.5px solid #222;padding:6px 8px}
td{padding:7px 8px;border-bottom:1px solid #e3e3e3;vertical-align:top}td.n{text-align:right;font-variant-numeric:tabular-nums}
tr.hl td{background:#fff6cc;font-weight:600}
.kv{display:grid;grid-template-columns:180px 1fr;gap:6px 16px;margin:8px 0 14px}.kv div:nth-child(odd){color:#666}
.box{border:1px solid #ddd;padding:14px 16px;margin:14px 0;background:#fafafa}
.stamp{display:inline-block;border:2px solid var(--c);color:var(--c);font-weight:700;letter-spacing:2px;padding:6px 14px;transform:rotate(-4deg);margin-top:18px}
.sig{margin-top:40px;font:italic 20px Georgia,serif;color:#334}.sig small{display:block;font:12px Helvetica;color:#666;font-style:normal}
.ft{position:absolute;left:64px;right:64px;bottom:34px;border-top:1px solid #ddd;padding-top:8px;font-size:10px;color:#888;display:flex;justify-content:space-between}
.crop{position:relative;overflow:hidden}.crop:before{content:'';position:absolute;left:0;top:0;bottom:0;width:118px;background:linear-gradient(90deg,#3a3a3a 0,#3a3a3a 92%,rgba(58,58,58,0));z-index:2}
.photo{transform:rotate(-1.2deg);box-shadow:0 2px 10px rgba(0,0,0,.25);padding:18px;background:#fdfdf8;margin:20px 0}"""


def page(color, org, orgsub, title, ref, body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>:root{{--c:{color}}}{CSS}</style></head><body><div class="page">
<div class="hd"><div class="org">{org}<small>{orgsub}</small></div><div class="ttl"><b>{title}</b>{ref}</div></div>
{body}<div class="ft"><span>Sample document for the Camunda credit operations demonstration. Fictional organisation and data.</span><span>Page 1 of 1</span></div></div></body></html>"""


DOCS = {
    # Jordan Ellis, US, late payment: bank shows the payment before the due date
    "D-26-10421": [("Bank statement (extracted)", "bank-statement-march-2026.pdf", page("#1f4e79", "Great Lakes Community Bank", "CHICAGO, IL",
        "Account statement", "1 to 31 March 2026<br>Checking ****6620", """
<div class="kv"><div>Account holder</div><div>Jordan Ellis</div><div>Statement period</div><div>1 March 2026 to 31 March 2026</div></div>
<table><tr><th>Date</th><th>Description</th><th>Reference</th><th class="n">Amount (USD)</th></tr>
<tr><td>2026-03-01</td><td>Opening balance</td><td></td><td class="n">2,184.55</td></tr>
<tr class="hl"><td>2026-03-02</td><td>Online payment to Meridian Card Services</td><td>MCS-4417-0302</td><td class="n">-412.00</td></tr>
<tr><td>2026-03-05</td><td>Payroll deposit</td><td>PAY-0305</td><td class="n">1,960.00</td></tr>
<tr><td>2026-03-11</td><td>Grocery store</td><td>POS 8812</td><td class="n">-86.41</td></tr>
<tr><td>2026-03-31</td><td>Closing balance</td><td></td><td class="n">3,646.14</td></tr></table>"""))],

    # Priya Raman, US, not her account: identity theft report and proof of address
    "D-26-10422": [("FTC identity theft report", "identity-theft-report-IDT-26-448120.pdf", page("#5a2a82", "Identity theft report", "CONSUMER COPY",
        "Report IDT-26-448120", "Filed online 2026-10-19", """
<div class="kv"><div>Reported by</div><div>Priya Raman</div><div>Address</div><div>18 Willow Lane, Columbus, OH</div><div>Date filed</div><div>2026-10-19</div></div>
<h2>Fraudulent account</h2><table><tr><th>Company</th><th>Account</th><th>Type</th><th>Opened</th></tr>
<tr class="hl"><td>Lakeshore Auto Finance</td><td>****9052</td><td>Auto loan</td><td>Not opened by me</td></tr></table>
<h2>Statement</h2><div class="box">I did not open this account and did not authorise anyone to open it. I first learned of it from my credit report in
October 2026. I have never lived at the address on the application.</div>
<div class="sig">Priya Raman<small>Signed electronically, 2026-10-19</small></div>""")),
                   ("Proof of address", "utility-bill-willow-lane.pdf", page("#2d6a4f", "Scioto Valley Utilities", "ELECTRIC AND GAS · COLUMBUS, OH",
        "Utility bill", "Account 44-1902-7 · October 2026", """
<div class="kv"><div>Customer</div><div>Priya Raman</div><div>Service address</div><div>18 Willow Lane, Columbus, OH</div><div>Customer since</div><div>2019</div></div>
<table><tr><th>Service</th><th>Period</th><th class="n">Amount (USD)</th></tr><tr><td>Electric</td><td>Sep 15 to Oct 14, 2026</td><td class="n">94.12</td></tr>
<tr><td>Gas</td><td>Sep 15 to Oct 14, 2026</td><td class="n">38.70</td></tr><tr><td><b>Total due</b></td><td></td><td class="n"><b>132.82</b></td></tr></table>"""))],

    # Thandiwe Mokoena, ZA, balance incorrect: EFT proof of payment
    "D-26-10423": [("Proof of payment (extracted)", "eft-proof-of-payment-UHF-88213.pdf", page("#8a5a00", "Marula Bank", "AUTHORISED FINANCIAL SERVICES PROVIDER · JOHANNESBURG",
        "Proof of payment", "EFT confirmation", """
<div class="kv"><div>Paid by</div><div>T Mokoena</div><div>Date</div><div>2026-09-28</div><div>Beneficiary</div><div>Ubuntu Home Furnishers</div>
<div>Beneficiary reference</div><div>UHF-88213</div><div>Amount</div><div><b>ZAR 5,500.00</b></div><div>Payment type</div><div>EFT (1 to 2 business days)</div></div>
<div class="box">This notification confirms that the payment above was processed from the payer's account. Funds reflect in the beneficiary account according to the beneficiary bank's clearing times.</div>
<div class="stamp">PAYMENT PROCESSED</div>"""))],

    # Lakshmi Iyer, IN, vulnerable: hospital stay and lender moratorium
    "D-26-10425": [("Hospital discharge summary", "discharge-summary.pdf", page("#0b6e78", "Cauvery General Hospital", "BENGALURU",
        "Discharge summary", "Inpatient record", """
<div class="kv"><div>Patient</div><div>Lakshmi Iyer</div><div>Admitted</div><div>2026-06-28</div><div>Discharged</div><div>2026-08-30</div></div>
<div class="box">Clinical details withheld from this copy at the patient's request. Issued to support a financial hardship claim.</div>
<div class="sig">Dr A. Menon<small>Consultant, Department of Medicine</small></div>""")),
                   ("Lender letter", "kaveri-finance-moratorium-letter.pdf", page("#7a1f2b", "Kaveri Finance", "CHENNAI",
        "Repayment moratorium", "Letter dated 2026-07-05", """
<p>Dear Ms Iyer,</p><p>Further to your request on 2026-07-02, we confirm a <b>repayment moratorium of three months, July to September 2026</b>, on
your loan account ****5508. No instalment is due for these months and none will be reported as missed.</p>
<p>Regular instalments resume in October 2026.</p><div class="sig">S. Raghavan<small>Customer Hardship Team, Kaveri Finance</small></div>"""))],

    # Oliver Hughes, UK, late payment: direct debit collected
    "D-26-10426": [("Direct debit confirmation", "albion-energy-direct-debit.pdf", page("#3b3b98", "Albion Energy", "LONDON",
        "Direct Debit confirmation", "Account ****1186", """
<div class="kv"><div>Customer</div><div>Oliver Hughes</div><div>Collection date</div><div>2026-08-12</div><div>Amount</div><div><b>GBP 96.40</b></div><div>Status</div><div>Collected</div></div>
<div class="box">Your Direct Debit payment has been collected in full. Thank you.</div>"""))],

    # Dana Whitfield, US, late payment: the upload has the date column cropped (why the evidence is partial)
    "D-26-10427": [("Bank statement (extracted)", "bank-statement-photo.pdf", page("#1f4e79", "Great Lakes Community Bank", "CHICAGO, IL",
        "Account statement", "Uploaded photo, page 2", """
<div class="photo crop"><table><tr><th style="width:118px">Date</th><th>Description</th><th>Reference</th><th class="n">Amount (USD)</th></tr>
<tr><td>2026-05-01</td><td>Coffee shop</td><td>POS 2210</td><td class="n">-6.25</td></tr>
<tr class="hl"><td>2026-05-05</td><td>Payment to Meridian Card Services</td><td>MCS-3820</td><td class="n">-120.00</td></tr>
<tr><td>2026-05-07</td><td>Payroll deposit</td><td>PAY-0507</td><td class="n">1,410.00</td></tr></table></div>"""))],
}


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 794, "height": 1123})
        for base, docs in DOCS.items():
            manifest[base] = []
            for n, (etype, fname, html) in enumerate(docs, 1):
                stem = f"{base}-{n}"
                pg.set_content(html)
                pg.pdf(path=os.path.join(OUT, stem + ".pdf"), width="794px", height="1123px", print_background=True)
                pg.screenshot(path=os.path.join(OUT, stem + ".png"), clip={"x": 0, "y": 0, "width": 794, "height": 1123})
                manifest[base].append({"type": etype, "fileName": fname, "file": stem + ".pdf", "thumb": stem + ".png"})
                print("wrote", stem, fname)
        b.close()
    json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
