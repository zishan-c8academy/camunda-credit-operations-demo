"""Put the demo back to its starting state before a run. Keeps closed cases (history for Optimize and the learn loop).

  python3 scripts/reset_demo.py            threshold back to 0.9 (new authority version), cancel open cases
  python3 scripts/reset_demo.py --keep-open   only reset the threshold
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import c8, build  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(ROOT, "dmn", "CB_DisputeApprovalAuthority.dmn")
open(path, "w").write(build.authority(0.9))
s, d = c8.deploy([path])
ver = d["deployments"][0]["decisionDefinition"]["version"] if s == 200 else d
print("authority table at 0.9, version", ver)
json.dump({"autoApplyMinConfidence": 0.9, "deployedVersion": ver}, open(os.path.join(ROOT, "data", "policy_state.json"), "w"))
s, cv = c8.call("GET", "/cluster-variables/global/CB_POLICY")
val = cv["value"] if isinstance(cv["value"], dict) else json.loads(cv["value"])
val["autoApplyMinConfidence"] = 0.9
print("CB_POLICY", c8.call("PUT", "/cluster-variables/global/CB_POLICY", {"value": val})[0])
if "--keep-open" not in sys.argv:
    for pid in ["CB_CreditDispute", "CB_IncidentManagement", "CB_RuleImprovement", "CB_RecognitionJourney"]:
        for pi in c8.search("/process-instances/search", {"processDefinitionId": pid, "state": "ACTIVE"}, size=100):
            print("cancel", pid, pi["processInstanceKey"], c8.call("POST", f"/process-instances/{pi['processInstanceKey']}/cancellation", {})[0])

if "--ledger" in sys.argv:
    import sqlite3
    con = sqlite3.connect(os.path.join(ROOT, "app", "cb.db"))
    con.execute("delete from ledger where event_type != 'OPENING_BALANCE'")
    con.execute("delete from badges where approved_by != 'auto' or employee not in ('Nomvula Khumalo','Arvind Rao') or at like '%T%'")
    con.commit()
    print("ledger reset to opening balances:", con.execute("select employee, sum(points) from ledger group by employee order by 2 desc").fetchall())
