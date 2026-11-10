"""One-time setup of a Camunda 8.10 SaaS cluster for this demo. Safe to re-run.

  python3 scripts/setup.py

1. Checks .env: cluster credentials, LLM endpoint and model.
2. Connects to the cluster and checks it runs 8.10 or later.
3. Creates the six cluster variables (CB_GENAI, CB_PROMPTS, CB_SYSTEMS, CB_CLOCK, CB_POLICY, CB_RECOGNITION).
4. Builds and deploys the 6 decision tables, 8 forms and 4 processes.

It cannot see connector secrets. Create CB_LLM_API_KEY in Console before you start a case (README, step 3).
"""
import glob, json, os, subprocess, sys

sys.stdout.reconfigure(line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import c8  # noqa: E402  (exits with a clear message when .env is missing)

print("1. configuration")
missing = [k for k in ("CB_LLM_ENDPOINT", "CB_LLM_MODEL") if not c8.cfg.get(k)]
if missing:
    sys.exit(f"   Add {' and '.join(missing)} to .env (see .env.example and README, step 4).")
print("   cluster", c8.cfg.get("CAMUNDA_CLUSTER_ID") or c8.BASE, "region", c8.cfg.get("CAMUNDA_CLUSTER_REGION") or "?")
print("   LLM", c8.cfg["CB_LLM_ENDPOINT"], "model", c8.cfg["CB_LLM_MODEL"])
if not c8.cfg.get("CAMUNDA_CLUSTER_REGION"):
    print("   WARNING: could not work out the cluster region; Operate links and the webhook URL in the workbench need it.")

print("2. connect")
try:
    s, d = c8.call("GET", "/topology")
except Exception as e:
    sys.exit(f"   Could not reach the cluster: {e}. Check the REST address and client credentials in .env.")
if s != 200:
    sys.exit(f"   Cluster answered {s}: {str(d)[:300]}. The client needs the Orchestration Cluster API scope.")
ver = d.get("gatewayVersion", "?")
print("   connected, Camunda", ver)
try:
    major, minor = (int(x) for x in ver.split(".")[:2])
    if (major, minor) < (8, 10):
        sys.exit("   This demo needs Camunda 8.10 or later (AI Agent sub-process v2, cluster variables, agent instances).")
except ValueError:
    pass

print("3. cluster variables")
r = subprocess.run([sys.executable, os.path.join(HERE, "setup_cluster.py")], cwd=ROOT)
if r.returncode:
    sys.exit("   Cluster variables failed (above). The client needs the Orchestration Cluster API scope.")

print("4. build and deploy models")
env = dict(os.environ, CB_LLM_SECRET_NAME=c8.cfg.get("CB_LLM_SECRET_NAME", "CB_LLM_API_KEY"))
subprocess.run([sys.executable, os.path.join(HERE, "build.py")], check=True, cwd=ROOT, env=env, stdout=subprocess.DEVNULL)
files = (sorted(glob.glob(os.path.join(ROOT, "dmn", "*.dmn"))) + sorted(glob.glob(os.path.join(ROOT, "forms", "*.form")))
         + sorted(glob.glob(os.path.join(ROOT, "bpmn", "*.bpmn"))))
s, d = c8.deploy(files)
if s != 200:
    sys.exit(f"   Deploy failed {s}: {json.dumps(d)[:600]}")
kinds = {}
for x in d.get("deployments", []):
    k = next((key for key, val in x.items() if val), "other")
    kinds[k] = kinds.get(k, 0) + 1
names = {"processDefinition": "processes", "decisionDefinition": "decisions", "decisionRequirements": "decision models", "form": "forms"}
print("   deployed", ", ".join(f"{n} {names.get(k, k)}" for k, n in kinds.items()))
json.dump({"autoApplyMinConfidence": 0.9}, open(os.path.join(ROOT, "data", "policy_state.json"), "w"))

print(f"\nDone. Next: create the connector secret {c8.cfg.get('CB_LLM_SECRET_NAME', 'CB_LLM_API_KEY')} if you have not (README, step 3), then")
print("   python3 app/server.py            # workbench and job workers on http://127.0.0.1:5690")
print("   python3 scripts/stage_demo.py    # in a second terminal: put work in every queue")
