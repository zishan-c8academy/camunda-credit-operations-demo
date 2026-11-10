"""Create or update the demo's cluster variables. Safe to re-run.

CB_GENAI        LLM endpoint and model, from CB_LLM_ENDPOINT and CB_LLM_MODEL in .env (any OpenAI-compatible API)
CB_PROMPTS      versioned system prompts (prompt registry)
CB_SYSTEMS      simulated bureau systems the agent tools read
CB_CLOCK        demo time compression for statutory and task clocks
CB_POLICY       change policy the learn loop must stay inside, and the live threshold
CB_RECOGNITION  recognition programme settings
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import c8

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_FILE = os.path.join(ROOT, "data", "policy_state.json")


def load(name):
    return json.load(open(os.path.join(ROOT, "data", name)))


def policy():
    state = json.load(open(POLICY_FILE)) if os.path.exists(POLICY_FILE) else {"autoApplyMinConfidence": 0.9}
    return {"decisionId": "CB_DisputeApprovalAuthority", "parameter": "autoApplyMinConfidence",
            "autoApplyMinConfidence": state["autoApplyMinConfidence"],
            "limits": {"min": 0.8, "max": 0.98, "maxStep": 0.05},
            "rules": "Only autoApplyMinConfidence (rule A-06) may change. Rules A-01 to A-05 (fraud, vulnerable consumers, adverse outcomes) are not tunable by the learn loop."}


VARS = {
    "CB_GENAI": {"endpoint": c8.cfg.get("CB_LLM_ENDPOINT", ""), "model": c8.cfg.get("CB_LLM_MODEL", ""),
                 "note": "OpenAI-compatible chat completions endpoint. In production this would be your own AI gateway."},
    "CB_PROMPTS": load("prompts.json"),
    "CB_SYSTEMS": load("systems.json"),
    "CB_CLOCK": {"secondsPerDay": 30, "secondsPerHour": 8, "secondsPerMinute": 4,
                 "note": "Demo time compression. 1 statutory day = 30 s, 1 working hour = 8 s, 1 minute = 4 s."},
    "CB_POLICY": policy(),
    "CB_RECOGNITION": {"dailyCap": 150, "programme": "Operations Guardians"},
}

if __name__ == "__main__":
    only = sys.argv[1:] or list(VARS)
    for name in only:
        value = VARS[name]
        s, d = c8.call("GET", f"/cluster-variables/global/{name}")
        if s == 200:
            s, d = c8.call("PUT", f"/cluster-variables/global/{name}", {"value": value})
            print("update", name, s, "" if s == 200 else d)
        else:
            s, d = c8.call("POST", "/cluster-variables/global", {"name": name, "value": value})
            print("create", name, s, "" if s == 200 else d)
