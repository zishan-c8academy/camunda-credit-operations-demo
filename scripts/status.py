"""Show a case: path taken, incidents, decisions, key variables.  python3 scripts/status.py <processInstanceKey> [--wait 120]"""
import json, sys, time, urllib.request

key = sys.argv[1]
wait = int(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 0
t0 = time.time()
while True:
    d = json.load(urllib.request.urlopen(f"http://127.0.0.1:5690/api/case/{key}", timeout=60))
    if "instance" not in d:
        time.sleep(3)
        continue
    st = d["instance"].get("state")
    active = [e["id"] for e in d["elements"] if e["state"] == "ACTIVE"]
    if st != "ACTIVE" or d["incidents"] or time.time() - t0 > wait or any(t["state"] == "CREATED" for t in d["tasks"]):
        break
    time.sleep(4)
v = d["variables"]
print("state", st, "| active:", active, "| %ds" % (time.time() - t0))
print("path:", " > ".join(e["id"] for e in d["elements"] if e["type"] not in ("SEQUENCE_FLOW",)))
for i in d["incidents"]:
    print("INCIDENT", i.get("elementId"), i.get("errorType"), (i.get("errorMessage") or "")[:300])
for dec in d["decisions"]:
    print("DMN", dec["decisionId"], "v%s" % dec["version"], [m["ruleId"] for m in dec["matchedRules"]], json.dumps(dec["result"])[:160])
for t in d["tasks"]:
    print("TASK", t["elementId"], t["state"], t.get("candidateGroups"), t.get("priority"), t.get("userTaskKey"))
for k in ("clock", "holidays", "furnisherResponse", "assessment", "authority", "decision", "triage", "severity", "proposal", "validation", "slaStatus"):
    if k in v:
        print(k, "=", json.dumps(v[k])[:700])
if d.get("agent"):
    a = d["agent"]
    print("agent", a.get("state"), json.dumps(a.get("metrics")))
    for s in a["steps"]:
        print("  ", s["type"], s.get("name", ""), json.dumps(s.get("args") or s.get("result") or s.get("text"))[:160])
