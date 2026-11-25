"""Compose file for one run: ERPNext (T9 layout) + mail + calendar + chat, all on one fake clock.

Pattern taken from codex T9.1 bootstrap.py (read, not imported): libfaketime preload with a shared offset
file; Synapse launched directly so its preload survives; postfix/dovecot keep the clock env.
Differences: one project per run, named volumes per run, no host ports except an optional loopback
ERPNext frontend, site created fresh (not cloned).
"""

from .docker import IMAGES

SITE = "norvale"
BENCH = "/home/frappe/frappe-bench"
CLOCK_ENV = {
    "LD_PRELOAD": "/opt/clock-lib/libfaketime.so.1",
    "FAKETIME_TIMESTAMP_FILE": "/clock/offset",
    "FAKETIME_NO_CACHE": "1",
    "FAKETIME_DONT_FAKE_MONOTONIC": "1",
    "FAKETIME_DISABLE_SHM": "1",
}
REDIS_ENV = {"FRAPPE_REDIS_CACHE": "redis://redis-cache:6379", "FRAPPE_REDIS_QUEUE": "redis://redis-queue:6379"}


def build(env_dir, lib_dir, frontend_port=None):
    clock = [f"{env_dir}/clock:/clock:ro", f"{lib_dir}:/opt/clock-lib:ro"]
    bench = ["sites:" + BENCH + "/sites", "logs:" + BENCH + "/logs"]
    base = {"restart": "unless-stopped", "networks": ["office"]}

    def erp(**kw):
        return {
            **base,
            "image": IMAGES["erp"],
            "volumes": bench + clock,
            "environment": {**CLOCK_ENV, **REDIS_ENV},
            **kw,
        }

    services = {
        "db": {
            **base,
            "image": IMAGES["db"],
            "environment": {"MARIADB_ROOT_PASSWORD": "admin", **CLOCK_ENV},
            "command": [
                "--character-set-server=utf8mb4",
                "--collation-server=utf8mb4_unicode_ci",
                "--skip-character-set-client-handshake",
            ],
            "volumes": ["db-data:/var/lib/mysql"] + clock,
        },
        "redis-cache": {**base, "image": IMAGES["redis"]},
        "redis-queue": {**base, "image": IMAGES["redis"]},
        "backend": erp(),
        "queue-short": erp(command=["bench", "worker", "--queue", "short,default"]),
        "queue-long": erp(command=["bench", "worker", "--queue", "long,default,short"]),
        "websocket": {
            **base,
            "image": IMAGES["erp"],
            "volumes": bench,
            "environment": REDIS_ENV,
            "command": ["node", BENCH + "/apps/frappe/socketio.js"],
        },
        "frontend": {
            **base,
            "image": IMAGES["erp"],
            "volumes": bench,
            "command": ["nginx-entrypoint.sh"],
            "environment": {
                "BACKEND": "backend:8000",
                "FRAPPE_SITE_NAME_HEADER": SITE,
                "SOCKETIO": "websocket:9000",
                "UPSTREAM_REAL_IP_ADDRESS": "127.0.0.1",
                "UPSTREAM_REAL_IP_HEADER": "X-Forwarded-For",
                "UPSTREAM_REAL_IP_RECURSIVE": "off",
                "PROXY_READ_TIMEOUT": "120",
                "CLIENT_MAX_BODY_SIZE": "50m",
            },
        },
        "mail": {
            **base,
            "image": IMAGES["mail"],
            "hostname": "mail.norvale.test",
            "environment": {
                "SSL_TYPE": "",
                "ENABLE_RSPAMD": "0",
                "ENABLE_CLAMAV": "0",
                "ENABLE_FAIL2BAN": "0",
                "ENABLE_AMAVIS": "0",
                "ENABLE_SPAMASSASSIN": "0",
                "ENABLE_OPENDKIM": "0",
                "ENABLE_OPENDMARC": "0",
                "ENABLE_POLICYD_SPF": "0",
                "SPOOF_PROTECTION": "1",
                "PERMIT_DOCKER": "none",
                "ONE_DIR": "1",
                **CLOCK_ENV,
            },
            "volumes": [
                f"{env_dir}/mail-config:/tmp/docker-mailserver",
                "mail-data:/var/mail",
                "mail-state:/var/mail-state",
            ]
            + clock,
        },
        "calendar": {
            **base,
            "image": IMAGES["calendar"],
            "environment": CLOCK_ENV,
            "volumes": clock + [f"{env_dir}/calendar:/config:ro", "calendar-data:/data"],
        },
        "matrix": {
            **base,
            "image": IMAGES["matrix"],
            "environment": CLOCK_ENV,
            "user": "1000:1000",
            "entrypoint": ["python", "-m", "synapse.app.homeserver"],
            "command": ["--config-path=/data/homeserver.yaml"],
            "volumes": clock + [f"{env_dir}/matrix:/data"],
        },
    }
    if frontend_port:
        services["frontend"]["ports"] = [f"127.0.0.1:{frontend_port}:8080"]
    volumes = ["db-data", "sites", "logs", "mail-data", "mail-state", "calendar-data"]
    return {"services": services, "networks": {"office": {}}, "volumes": {v: {} for v in volumes}}
