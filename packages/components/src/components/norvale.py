"""Basic Norvale identities expressed as reusable component declarations."""

from .registry import VERSION


def _descriptor(type_id, config, state):
    return {
        "version": VERSION,
        "type": type_id,
        "instance_schema": config,
        "state_schema": state,
        "handles": {},
        "emits": {},
        "parameters": {},
        "policies": {},
        "relations": {},
        "requires_adapters": [],
        "provides_adapters": [],
        "provenance": "Norvale environment",
    }


SITE = _descriptor("norvale.site.v1", {"name": "string"}, {"open": "boolean"})
ROLE = _descriptor("norvale.role.v1", {"name": "string"}, {"active": "boolean"})
PRODUCT = _descriptor("norvale.product.v1", {"sku": "string"}, {"released": "boolean"})
CUSTOMER = _descriptor("norvale.customer.v1", {"name": "string"}, {"active": "boolean"})

DESCRIPTORS = (SITE, ROLE, PRODUCT, CUSTOMER)
