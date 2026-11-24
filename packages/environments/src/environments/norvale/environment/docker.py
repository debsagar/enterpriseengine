"""Thin wrappers around the docker CLI (default context, data root /data/docker)."""
import json
import subprocess

from components.images import PINS

IMAGES = {
    "erp": PINS["erpnext_clock"],           # frappe/erpnext v15.124.1 + libfaketime 0.9.13 (Dockerfile.erp, copied from codex T9.1)
    "calendar": PINS["calendar"],    # Radicale 3.9.0
    "mail": PINS["mail"],
    "matrix": PINS["matrix"],
    "db": "mariadb:10.6",
    "redis": "redis:7-alpine",
}


def run(args, **kw):
    kw.setdefault("check", True)
    return subprocess.run(["docker", *args], **kw)


def out(args):
    return run(args, capture_output=True, text=True).stdout


def compose(project, file, *args, **kw):
    return run(["compose", "-p", project, "-f", str(file), *args], **kw)


def container_ip(project, service):
    name = out(["ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
                "--filter", f"label=com.docker.compose.service={service}"]).split()
    if not name:
        raise RuntimeError(f"{project}/{service} is not running")
    nets = json.loads(out(["inspect", "-f", "{{json .NetworkSettings.Networks}}", name[0]]))
    return next(iter(nets.values()))["IPAddress"]


def exec_(project, file, service, *cmd, **kw):
    return compose(project, file, "exec", "-T", service, *cmd, **kw)
