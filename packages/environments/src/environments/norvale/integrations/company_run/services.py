"""Operate existing run containers using their Compose ownership labels."""
import json
import subprocess

from components.images import require_image

from environments.norvale.native.company import project
from environments.norvale.paths import SOURCE_LAYOUT


APPLICATIONS = {
    "erp": {**dict.fromkeys(("backend", "queue-short", "queue-long", "websocket", "frontend"),
                           "erpnext_clock"), "tickets": "gitea", "mail": "mail",
            "matrix": "matrix", "calendar": "calendar"},
    "mes": {"odoo": "odoo"},
    "plm": {"plm": "inventree"},
    "qms": {"qms": "qms"},
}


def manage(runtime, action, *, verify_images=False):
    release = runtime / "release"
    source = release / SOURCE_LAYOUT
    integrations = (source if source.is_dir() else release) / "integrations"
    files = {
        "erp": runtime / "business/env/compose.json",
        "mes": integrations / "mes/compose.yaml",
        "plm": integrations / "plm/compose.yaml",
        "qms": integrations / "qms/compose.yml",
    }
    sources = runtime / "services.json"
    names = {"erp": project(runtime / "business")}
    if sources.is_file():
        cfg = json.loads(sources.read_text())
        names.update(mes=cfg["mes_project"], plm=cfg["plm_project"])
    env = runtime / "qms/docker.env"
    if env.is_file():
        qms = [line.partition("=")[2] for line in env.read_text().splitlines()
               if line.startswith("COMPOSE_PROJECT_NAME=")]
        if len(qms) != 1 or not qms[0]:
            raise ValueError("Missing or ambiguous commissioned QMS project")
        names["qms"] = qms[0]
    rows = []
    for system, file in files.items():
        if not file.is_file():
            continue
        command = ["docker", "ps", "-a", "--no-trunc", "--filter", "label=com.docker.compose.oneoff=False",
                   "--filter", f"label=com.docker.compose.project.config_files={file}"]
        if system in names:
            command += ["--filter", f"label=com.docker.compose.project={names[system]}"]
        # During initial MES/PLM installation the run-local Compose file may be
        # the only ownership evidence. Refuse competing projects on that file.
        fmt = '{{.ID}} {{.State}} {{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.service"}}'
        records = [line.split() for line in subprocess.check_output(
            [*command, "--format", fmt], text=True).splitlines()]
        if len({row[2] for row in records}) > 1:
            raise ValueError(f"Ambiguous {system} projects for {file}")
        for container, state, project_name, service in records:
            app = APPLICATIONS[system].get(service)
            if verify_images and app:
                image = subprocess.check_output(
                    ["docker", "inspect", "--format", "{{.Config.Image}}", container], text=True).strip()
                require_image(app, image)
            rows.append(dict(id=container, state=state, project=project_name, service=service))
    if action == "status":
        return rows
    infrastructure = {"db", "redis", "redis-cache", "redis-queue"}
    if not rows:
        raise ValueError("No existing containers found for this run on the current Docker daemon")
    tiers = (True, False) if action == "start" else (False, True)
    eligible = {"exited", "created"} if action == "start" else {"running", "restarting"}
    for dependencies in tiers:
        ids = [row["id"] for row in rows if row["state"] in eligible
               and (row["service"] in infrastructure) == dependencies]
        if ids:
            subprocess.run(["docker", action, *ids], check=True)
    return rows
