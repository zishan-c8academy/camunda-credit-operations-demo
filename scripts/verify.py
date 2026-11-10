"""Drive every route through the live cluster and check it against the expected outcome. Needs app/server.py running.

  python3 scripts/verify.py disputes [--rounds 1]   all dispute scenarios
  python3 scripts/verify.py seed --n 6               history for the learn loop (partial-evidence approvals + one override)
  python3 scripts/verify.py incidents                all alerts, acknowledged and resolved
  python3 scripts/verify.py improvement              learn loop: propose, approve, deploy
Add --wait 900 (seconds per step, default 420) on a slow or busy cluster.
Prints a PASS/FAIL matrix and writes docs/VERIFY_LOG.md.
"""
import json, os, sys, threading, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c8  # noqa: E402

API = "http://127.0.0.1:" + c8.cfg.get("CB_PORT", "5690")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = []
lock = threading.Lock()


def get(p):
    return json.load(urllib.request.urlopen(API + p, timeout=90))


def post(p, b):
    r = urllib.request.Request(API + p, data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(r, timeout=90))


def case(k):
    for _ in range(40):
        d = get(f"/api/case/{k}")
        if "instance" in d:
            return d
        time.sleep(2)
    raise RuntimeError(f"case {k} not visible")


WAIT = 420   # seconds to wait for each step; raise it with --wait on a slow or busy cluster


def wait(k, cond, timeout=240, step=4):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = case(k)
        r = cond(d)
        if r:
            return d, r
        time.sleep(step)
    return case(k), None


def open_task(d, ids):
    return next((t for t in d["tasks"] if t["state"] == "CREATED" and t["elementId"] in ids), None)


PEOPLE = {"dispute-analysts": "Nomvula Khumalo", "senior-analysts": "Arvind Rao", "fraud-compliance": "Megan Brooks", "compliance-officers": "Megan Brooks",
          "dispute-specialists": "Nomvula Khumalo"}


def run_dispute(name, decision="APPROVE", override=None, extra=None, tag=""):
    sc = json.load(open(os.path.join(ROOT, "data", "scenarios.json")))["disputes"][name]
    exp = sc["expect"]
    r = post("/api/disputes/start", {"scenario": name, "variables": extra or {}})
    k, did = r["processInstanceKey"], r["disputeId"]
    t0 = time.time()
    notes = []

    def step(d):
        if d["instance"]["state"] != "ACTIVE":
            return "done"
        if d["incidents"]:
            return "incident"
        t = open_task(d, ["ReviewRecommendation", "AskSpecialist", "ComplianceBreachReview"])
        return t and t["elementId"]
    while True:
        d, what = wait(k, step, timeout=WAIT)
        if what in (None, "done"):
            break
        if what == "incident":
            notes.append("incident: " + d["incidents"][0].get("errorMessage", "")[:60])
            post(f"/api/ops/unlock/{did}", {})
            post(f"/api/ops/retry/{k}", {})
            time.sleep(6)
            continue
        t = open_task(d, [what])
        who = PEOPLE.get((t.get("candidateGroups") or ["dispute-analysts"])[0], "Nomvula Khumalo")
        if what == "ReviewRecommendation":
            notes.append(f"review by {t.get('candidateGroups')}")
            vars_ = {"analystDecision": decision, "reviewerName": who, "consumerLetter": (d["variables"].get("assessment") or {}).get("consumerLetter", "")}
            if decision == "OVERRIDE":
                vars_.update(overrideOutcome=override[0], overrideReason=override[1])
        elif what == "AskSpecialist":
            notes.append("agent asked a specialist")
            vars_ = {"specialistAnswer": "The furnisher application used an address in Nevada never linked to the consumer; treat as identity theft."}
        else:
            notes.append("statutory breach review")
            vars_ = {"complianceAction": "LOG_EXCEPTION"}
        post(f"/api/tasks/{t['userTaskKey']}/complete", {"variables": vars_, "assignee": who})
        time.sleep(3)
    v = d["variables"]
    a, au, dec = v.get("assessment") or {}, v.get("authority") or {}, v.get("decision") or {}
    route = v.get("obsRoute") or au.get("route")
    ok = d["instance"]["state"] == "COMPLETED" and route == exp["route"] and (not exp.get("rule") or au.get("ruleId") == exp["rule"]) \
        and (not exp.get("recommendation") or a.get("recommendation") == exp["recommendation"]) \
        and (not exp.get("group") or au.get("reviewGroup") == exp["group"])
    m = (d.get("agent") or {}).get("metrics") or {}
    row = {"scenario": name + tag, "dispute": did, "key": k, "pass": ok, "state": d["instance"]["state"], "route": route, "rule": au.get("ruleId"),
           "group": au.get("reviewGroup"), "recommendation": a.get("recommendation"), "score": a.get("confidence"), "outcome": dec.get("outcome"),
           "mode": dec.get("mode"), "modelCalls": m.get("modelCalls"), "seconds": round(time.time() - t0), "notes": "; ".join(notes),
           "expected": exp}
    with lock:
        RESULTS.append(row)
    print(("PASS " if ok else "FAIL ") + json.dumps({k2: row[k2] for k2 in ("scenario", "dispute", "route", "rule", "group", "recommendation", "score", "mode", "seconds", "notes")}), flush=True)
    return row


def run_incident(name):
    sc = json.load(open(os.path.join(ROOT, "data", "scenarios.json")))["incidents"][name]
    r = post("/api/incidents/alert", {"scenario": name})
    inc = r["incidentId"]
    t0 = time.time()
    k = None
    while time.time() - t0 < 60 and not k:   # webhook starts have no key: find it by incidentId
        for c in get("/api/incidents"):
            if c["vars"].get("incidentId") == inc:
                k = c["key"]
        time.sleep(3)
    notes = [f"via {r['via']}"]
    done = set()
    while True:
        d, what = wait(k, lambda d: "done" if d["instance"]["state"] != "ACTIVE" else ((open_task(d, ["Acknowledge", "Resolve", "AskOnCall"]) or {}).get("elementId")), timeout=300)
        if what in (None, "done"):
            break
        t = open_task(d, [what])
        if what == "Acknowledge":
            vars_ = {"ackNote": "On it"}
        elif what == "Resolve":
            vars_ = {"resolution": "Rolled back the change", "rootCause": (d["variables"].get("triage") or {}).get("suspectedCause", ""), "triageAccurate": "YES", "resolverName": "Karthik Subramanian"}
        else:
            vars_ = {"onCallAnswer": "Pool saturation confirmed at 100% since 07:43."}
        post(f"/api/tasks/{t['userTaskKey']}/complete", {"variables": vars_, "assignee": "Karthik Subramanian"})
        done.add(what)
        time.sleep(3)
    v = d["variables"]
    sev, tr = v.get("severity") or {}, v.get("triage") or {}
    ok = d["instance"]["state"] == "COMPLETED" and sev.get("priority") == sc["expect"]["priority"] and bool(sev.get("majorIncident")) == sc["expect"]["major"]
    row = {"scenario": "incident:" + name, "dispute": inc, "key": k, "pass": ok, "state": d["instance"]["state"], "route": sev.get("priority"), "rule": sev.get("ruleId"),
           "group": None, "recommendation": f"{tr.get('impact')}/{tr.get('urgency')}", "score": tr.get("confidence"), "outcome": tr.get("linkedChange"),
           "mode": "major" if sev.get("majorIncident") else "", "modelCalls": ((d.get("agent") or {}).get("metrics") or {}).get("modelCalls"),
           "seconds": round(time.time() - t0), "notes": "; ".join(notes + [f"flagged {v.get('flaggedDisputes')}" if v.get("flaggedDisputes") is not None else ""]),
           "expected": sc["expect"]}
    with lock:
        RESULTS.append(row)
    print(("PASS " if ok else "FAIL ") + json.dumps({k2: row[k2] for k2 in ("scenario", "dispute", "route", "rule", "recommendation", "outcome", "mode", "seconds", "notes")}), flush=True)


def run_improvement(approve=True):
    r = post("/api/improvements/start", {"requestedBy": "verify"})
    k = r["processInstanceKey"]
    t0 = time.time()
    d, what = wait(k, lambda d: "done" if d["instance"]["state"] != "ACTIVE" else ((open_task(d, ["RuleOwnerReview"]) or {}).get("elementId")), timeout=WAIT)
    p = d["variables"].get("proposal") or {}
    print("proposal", json.dumps(p)[:600])
    print("validation", d["variables"].get("validation"))
    if what == "RuleOwnerReview":
        t = open_task(d, ["RuleOwnerReview"])
        post(f"/api/tasks/{t['userTaskKey']}/complete", {"variables": {"ruleOwnerDecision": "APPROVE" if approve else "REJECT", "ruleOwnerComments": "Backtest clean"}, "assignee": "David Mensah"})
        d, _ = wait(k, lambda d: d["instance"]["state"] != "ACTIVE", timeout=120)
    v = d["variables"]
    ok = d["instance"]["state"] == "COMPLETED" and (v.get("deployedVersion") is not None or not approve)
    row = {"scenario": "improvement", "dispute": "-", "key": k, "pass": ok, "state": d["instance"]["state"], "route": "deployed v%s" % v.get("deployedVersion"),
           "rule": "A-06", "group": None, "recommendation": f"{p.get('currentValue')} -> {p.get('proposedValue')}", "score": None, "outcome": json.dumps(p.get("backtest")),
           "mode": "", "modelCalls": None, "seconds": round(time.time() - t0), "notes": (p.get("rationale") or "")[:200], "expected": {"deployed": True}}
    RESULTS.append(row)
    print(("PASS " if ok else "FAIL ") + json.dumps({k2: row[k2] for k2 in ("route", "recommendation", "outcome", "seconds")}))


def parallel(jobs):
    ts = [threading.Thread(target=j) for j in jobs]
    for t in ts:
        t.start()
        time.sleep(1.5)
    for t in ts:
        t.join()


def write_log(title):
    path = os.path.join(ROOT, "docs", "VERIFY_LOG.md")
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("# Verification log\n\nEach run drives real process instances on your cluster with the live model.\n")
        f.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')} {title}\n\n| Result | Scenario | Case | Route | Rule | Group | AI | Score | Outcome | Seconds | Notes |\n|---|---|---|---|---|---|---|---|---|---|---|\n")
        for r in RESULTS:
            f.write(f"| {'PASS' if r['pass'] else 'FAIL'} | {r['scenario']} | {r['dispute']} | {r['route']} | {r['rule']} | {r['group'] or ''} | {r['recommendation']} | {r['score'] if r['score'] is not None else ''} | {r['mode'] or r['outcome'] or ''} | {r['seconds']} | {r['notes']} |\n")
    print(f"\n{sum(r['pass'] for r in RESULTS)}/{len(RESULTS)} passed. Log: docs/VERIFY_LOG.md")


if __name__ == "__main__":
    what = sys.argv[1]
    rounds = int(sys.argv[sys.argv.index("--rounds") + 1]) if "--rounds" in sys.argv else 1
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 6
    WAIT = int(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else WAIT
    if what == "disputes":
        jobs = []
        for _ in range(rounds):
            jobs += [lambda: run_dispute("latePayment"), lambda: run_dispute("identityFraud"), lambda: run_dispute("balanceTiming"),
                     lambda: run_dispute("vulnerable"), lambda: run_dispute("systemLocked"), lambda: run_dispute("partialEvidence"),
                     lambda: run_dispute("noResponse", extra={"clockSecondsPerDay": 3})]
        parallel(jobs)
    elif what == "seed":
        jobs = [lambda i=i: run_dispute("partialEvidence", tag=f"#seed{i}") for i in range(n)]
        jobs.append(lambda: run_dispute("balanceTiming", decision="OVERRIDE",
                                        override=("CORRECT", "Furnisher ledger shows the payment cleared before the statement run; correct the balance to ZAR 9,320."), tag="#override"))
        parallel(jobs)
    elif what == "incidents":
        parallel([lambda: run_incident("disputeLatency"), lambda: run_incident("ingestDelay"), lambda: run_incident("certExpiry")])
    elif what == "improvement":
        run_improvement()
    write_log(" ".join(sys.argv[1:]))
