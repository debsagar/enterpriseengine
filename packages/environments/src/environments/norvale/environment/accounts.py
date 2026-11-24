"""Mail, calendar and chat accounts for every identity (employees and outside parties).

identity = {"id": "emp:05", "mail": "aisha.bello@norvale.test", "chat": "aisha.bello"}; passwords are generated
here and kept only in env/secrets.json (0600). Never print that file.
"""
import hashlib
import hmac
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def make_secrets(env_dir, identities):
    path = Path(env_dir) / "secrets.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    for i in identities:
        data.setdefault(i["id"], {"password": secrets.token_urlsafe(15)})
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return data


def write_mail_config(env_dir, identities, sec):
    cfg = Path(env_dir) / "mail-config"
    cfg.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in identities:
        hashed = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=sec[i["id"]]["password"] + "\n",
                                text=True, capture_output=True, check=True).stdout.strip()
        lines.append(f"{i['mail']}|{{SHA512-CRYPT}}{hashed}")
    (cfg / "postfix-accounts.cf").write_text("\n".join(lines) + "\n")
    # Every domain is local; nothing can leave the stack.
    clock_vars = "LD_PRELOAD FAKETIME_TIMESTAMP_FILE FAKETIME_NO_CACHE FAKETIME_DONT_FAKE_MONOTONIC FAKETIME_DISABLE_SHM"
    (cfg / "postfix-main.cf").write_text(
        "default_transport = error:External delivery disabled\nrelay_transport = error:External relay disabled\n"
        "smtpd_sasl_auth_enable = yes\nsmtpd_tls_auth_only = no\nmessage_size_limit = 20480000\n"
        f"import_environment = MAIL_CONFIG MAIL_DEBUG MAIL_LOGTAG TZ LANG=C POSTLOG_SERVICE POSTLOG_HOSTNAME {clock_vars}\n")
    (cfg / "dovecot.cf").write_text("auth_allow_cleartext = yes\nimport_environment {\n"
                                    + "".join(f"  {k} = %{{env:{k}}}\n" for k in clock_vars.split()) + "}\n")


def write_calendar_config(env_dir, identities, sec):
    cal = Path(env_dir) / "calendar"
    cal.mkdir(parents=True, exist_ok=True)
    (cal / "users").write_text("".join(f"{i['chat']}:{sec[i['id']]['password']}\n" for i in identities))
    (cal / "config").write_text("[server]\nhosts = 0.0.0.0:5232\n[auth]\ntype = htpasswd\nhtpasswd_filename = /config/users\n"
                                "htpasswd_encryption = plain\n[rights]\ntype = owner_only\n[storage]\n"
                                "filesystem_folder = /data/collections\n")


def write_matrix_config(env_dir, image):
    mx = Path(env_dir) / "matrix"
    mx.mkdir(parents=True, exist_ok=True)
    conf = mx / "homeserver.yaml"
    if conf.exists():
        return
    subprocess.run(["docker", "run", "--rm", "-e", "UID=1000", "-e", "GID=1000", "-e", "SYNAPSE_SERVER_NAME=norvale.test",
                    "-e", "SYNAPSE_REPORT_STATS=no", "-v", f"{mx}:/data", image, "generate"],
                   check=True, capture_output=True)
    with conf.open("a") as f:  # no federation; test-scale rate limits off
        f.write("\nfederation_domain_whitelist: []\nrc_message:\n  per_second: 1000\n  burst_count: 10000\n"
                "rc_registration:\n  per_second: 1000\n  burst_count: 10000\nrc_login:\n  address:\n    per_second: 1000\n"
                "    burst_count: 10000\n  account:\n    per_second: 1000\n    burst_count: 10000\n")


def register_chat(env_dir, base_url, identities, sec):
    """Register each identity on Synapse with the shared secret; store its access token in secrets."""
    def req(path, body=None):
        r = urllib.request.Request(base_url + path, data=None if body is None else json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.load(resp)
    for _ in range(120):
        try:
            req("/_matrix/client/versions")
            break
        except (OSError, urllib.error.HTTPError):
            time.sleep(1)
    else:
        raise RuntimeError("chat server not ready")
    secret = next(l.split(":", 1)[1].strip().strip("\"'") for l in
                  (Path(env_dir) / "matrix/homeserver.yaml").read_text().splitlines()
                  if l.startswith("registration_shared_secret:"))
    for i in identities:
        s = sec[i["id"]]
        if "chat_token" in s:
            continue
        nonce = req("/_synapse/admin/v1/register")["nonce"]
        mac = hmac.new(secret.encode(), "\0".join([nonce, i["chat"], s["password"], "notadmin"]).encode(),
                       hashlib.sha1).hexdigest()
        res = req("/_synapse/admin/v1/register", dict(nonce=nonce, username=i["chat"], password=s["password"],
                                                       admin=False, mac=mac))
        s["chat_token"], s["chat_user"] = res["access_token"], res["user_id"]
    path = Path(env_dir) / "secrets.json"
    path.write_text(json.dumps(sec))
    path.chmod(0o600)
