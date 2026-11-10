"""Stage the demo: reset, redeploy and put work in every queue. Run with app/server.py running, shortly before you show it.

  python3 scripts/stage_demo.py

1. Resets: authority threshold back to 0.9 (new version), open cases cancelled, ledger to opening balances.
2. Redeploys every model (so the webhook binds to the newest incident version with nothing running on older ones).
3. Stages work so queues are not empty: a fraud case for Megan, a vulnerable-consumer case for Arvind, a partial-evidence
   case for Nomvula, a locked credit file (incident in Operate), a furnisher that never answers (fast clock, deleted as
   unverified by showtime), and two straight-through late payments.
4. Checks: webhook answers, every staged case reached its expected state, prints the Operate links.
"""
import glob, json, os, subprocess, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import c8  # noqa: E402

API = "http://127.0.0.1:" + c8.cfg.get("CB_PORT", "5690")


def post(p, b):
    r = urllib.request.Request(API + p, data=json.dumps(b).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(r, timeout=90))


def get(p):
    return json.load(urllib.request.urlopen(API + p, timeout=90))


print("1. reset")
subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "reset_demo.py"), "--ledger"], check=True)
print("2. deploy")
env = dict(os.environ, CB_LLM_SECRET_NAME=c8.cfg.get("CB_LLM_SECRET_NAME", "CB_LLM_API_KEY"))
subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build.py")], check=True, env=env, stdout=subprocess.DEVNULL)
files = sorted(glob.glob(os.path.join(ROOT, "dmn", "*.dmn"))) + sorted(glob.glob(os.path.join(ROOT, "forms", "*.form"))) + sorted(glob.glob(os.path.join(ROOT, "bpmn", "*.bpmn")))
s, d = c8.deploy(files)
print("   deploy", s)
time.sleep(20)   # connectors runtime picks up the new webhook version

print("3. stage")
staged = {}
SLOW = {"clockSecondsPerDay": 300, "clockSecondsPerHour": 600}   # staged cases must not escalate before the session
for name, extra in [("identityFraud", SLOW), ("vulnerable", SLOW), ("partialEvidence", SLOW), ("systemLocked", SLOW),
                    ("noResponse", {"clockSecondsPerDay": 3}), ("latePayment", SLOW)]:
    r = post("/api/disputes/start", {"scenario": name, "variables": extra})
    staged[name] = r
    print("  ", name, r.get("disputeId"), r.get("processInstanceKey"))
    time.sleep(2)

print("4. wait for staged cases (up to 3 minutes)")
want = {"identityFraud": "task", "vulnerable": "task", "partialEvidence": "task", "systemLocked": "incident", "noResponse": "done", "latePayment": "done"}
t0 = time.time()
state = {}
while time.time() - t0 < 180 and len(state) < len(want):
    for name, r in staged.items():
        if name in state:
            continue
        cd = get(f"/api/case/{r['processInstanceKey']}")
        if "instance" not in cd:
            continue
        if want[name] == "task" and any(t["state"] == "CREATED" for t in cd["tasks"]):
            state[name] = "task waiting: " + ",".join(t["candidateGroups"][0] for t in cd["tasks"] if t["state"] == "CREATED")
        elif want[name] == "incident" and cd["incidents"]:
            state[name] = "incident in Operate"
        elif want[name] == "done" and cd["instance"]["state"] == "COMPLETED":
            state[name] = "completed, route " + str(cd["variables"].get("obsRoute"))
    time.sleep(5)
for name in want:
    print(f"   {'OK  ' if name in state else 'WAIT'} {name}: {state.get(name, 'not yet')}")

print("5. webhook")
cfg = get("/api/config")
try:
    req = urllib.request.Request(cfg["webhook"], data=b'{"probe":true}', method="POST", headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (cb-preflight)"})
    print("   webhook", urllib.request.urlopen(req, timeout=20).status, "(a probe incident starts; it is cancelled next)")
    time.sleep(8)
    for pi in c8.search("/process-instances/search", {"processDefinitionId": "CB_IncidentManagement", "state": "ACTIVE"}, size=10):
        c8.call("POST", f"/process-instances/{pi['processInstanceKey']}/cancellation", {})
except Exception as e:
    print("   WEBHOOK FAILED:", e, "-> the UI falls back to starting incidents by API")
print("\nReady. Workbench http://127.0.0.1:5690  Operate", cfg["operate"], " Tasklist", cfg["tasklist"])
