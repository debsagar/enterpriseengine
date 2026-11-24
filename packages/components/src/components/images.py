"""Image references used by supported application components."""

PINS = {
    "erpnext": "frappe/erpnext:v15.124.1",
    "erpnext_clock": "nv11-erp:15.124.1",
    "qms": "frappe/erpnext:v15.124.1",
    "inventree": "inventree/inventree:1.3.6@sha256:8978188efb0e48f88aa15a2ef9a7f6c4c8d039ff76a9b836e54648fcb96a1e73",
    "odoo": "odoo:18.0@sha256:d0b20aa4ec8a4d97ca096ada36275d77963d571c1cb1cceca69faf6098326c83",
    "gitea": "gitea/gitea:1.22.6",
    "mail": "ghcr.io/docker-mailserver/docker-mailserver:16.0.1",
    "matrix": "ghcr.io/element-hq/synapse:v1.164.1",
    "calendar": "nv11-calendar:3.9.0",
}


def require_image(application, image):
    expected = PINS[application]
    if image != expected:
        raise ValueError(f"{application} image mismatch; requires {expected}, found {image!r}")
