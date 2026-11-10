"""Small Camunda 8 REST client used by the scripts and the workbench.

Credentials come from .env in the repo root: the "Env Vars" file Camunda Console gives you when you create an API
client (export lines and quotes are fine). Set C8_ENV to use another file. Real environment variables win over the file.

  python3 scripts/c8.py GET  /topology
  python3 scripts/c8.py POST /process-instances '{"processDefinitionId":"X"}'
  python3 scripts/c8.py deploy bpmn/a.bpmn dmn/b.dmn
"""
import json, os, re, sys, time, uuid, urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.environ.get("C8_ENV") or os.path.join(ROOT, ".env")
cfg = {}
if os.path.exists(ENV):
    for line in open(ENV):
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip("'\"")
cfg.update({k: v for k, v in os.environ.items() if k.startswith(("CAMUNDA_", "ZEEBE_", "CB_"))})


def pick(*names, default=None):
    return next((cfg[n] for n in names if cfg.get(n)), default)


REST = pick("CAMUNDA_REST_ADDRESS", "ZEEBE_REST_ADDRESS", "CAMUNDA_BASE_URL")
if not REST and cfg.get("CAMUNDA_CLUSTER_ID") and cfg.get("CAMUNDA_CLUSTER_REGION"):
    REST = f"https://{cfg['CAMUNDA_CLUSTER_REGION']}.zeebe.camunda.io/{cfg['CAMUNDA_CLUSTER_ID']}"
CLIENT_ID = pick("CAMUNDA_CLIENT_ID", "ZEEBE_CLIENT_ID")
CLIENT_SECRET = pick("CAMUNDA_CLIENT_SECRET", "ZEEBE_CLIENT_SECRET")
if not (REST and CLIENT_ID and CLIENT_SECRET):
    sys.exit(f"No cluster credentials found. Save the client credentials from Camunda Console as {ENV} (see README, step 2).")
OAUTH_URL = pick("CAMUNDA_OAUTH_URL", "ZEEBE_AUTHORIZATION_SERVER_URL", default="https://login.cloud.camunda.io/oauth/token")
AUDIENCE = pick("CAMUNDA_TOKEN_AUDIENCE", "ZEEBE_TOKEN_AUDIENCE", default="zeebe.camunda.io")
_m = re.match(r"https://([a-z0-9-]+)\.zeebe\.camunda\.io/([0-9a-f-]{36})", REST)
cfg.setdefault("CAMUNDA_CLUSTER_REGION", _m.group(1) if _m else "")
cfg.setdefault("CAMUNDA_CLUSTER_ID", _m.group(2) if _m else "")
BASE = REST.rstrip("/") if REST.rstrip("/").endswith("/v2") else REST.rstrip("/") + "/v2"
UA = "Mozilla/5.0 (cb-camunda-demo)"
_tok = {"v": None, "exp": 0}


def token():
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    form = urllib.parse.urlencode({"grant_type": "client_credentials", "audience": AUDIENCE,
                                   "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}).encode()
    d = json.load(urllib.request.urlopen(urllib.request.Request(OAUTH_URL, data=form, headers={"User-Agent": UA}), timeout=30))
    _tok.update(v=d["access_token"], exp=time.time() + d.get("expires_in", 3600))
    return _tok["v"]


def call(method, path, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": ctype, "Accept": "application/json", "User-Agent": UA})
    for attempt in range(4):  # SaaS gateways return the odd 502/503/504; retry those with backoff
        try:
            r = urllib.request.urlopen(req, timeout=60)
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else None)
        except urllib.error.HTTPError as e:
            txt = e.read().decode()
            safe = method == "GET" or path.endswith("/search")  # never retry writes: they could run twice
            if e.code in (502, 503, 504) and attempt < 3 and safe:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                return e.code, json.loads(txt)
            except ValueError:
                return e.code, txt
        except urllib.error.URLError:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise


def deploy(paths):
    b = "----c8" + uuid.uuid4().hex
    parts = []
    for p in paths:
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"resources\"; filename=\"{os.path.basename(p)}\"\r\n"
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + open(p, "rb").read() + b"\r\n")
    raw = b"".join(parts) + f"--{b}--\r\n".encode()
    return call("POST", "/deployments", raw=raw, ctype="multipart/form-data; boundary=" + b)


def search(path, flt=None, size=100, sort=None):
    body = {"filter": flt or {}, "page": {"limit": size}}
    if sort:
        body["sort"] = sort
    s, d = call("POST", path, body)
    if s != 200:
        raise RuntimeError(f"{path} {s} {d}")
    return d.get("items", [])


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "deploy":
        s, d = deploy(a[1:])
    else:
        s, d = call(a[0], a[1], json.loads(a[2]) if len(a) > 2 else None)
    print(s)
    print(json.dumps(d, indent=1)[:6000])
