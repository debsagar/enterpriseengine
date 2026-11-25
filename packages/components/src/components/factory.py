"""Small reusable factory component declarations."""

from .registry import VERSION


def _descriptor(type_id, config, state, *, relations=None, handles=None, emits=None):
    return {
        "version": VERSION,
        "type": type_id,
        "instance_schema": config,
        "state_schema": state,
        "handles": handles or {},
        "emits": emits or {},
        "parameters": {},
        "policies": {},
        "relations": relations or {},
        "requires_adapters": [],
        "provides_adapters": [],
        "provenance": "generic factory component",
    }


WORKSTATION = _descriptor(
    "factory.workstation.v1",
    {"name": "string", "capacity": "integer"},
    {"available": "boolean"},
    handles={"work_started": {"work_order": "string"}},
    emits={"work_completed": {"work_order": "string"}},
)
WAREHOUSE = _descriptor(
    "factory.warehouse.v1",
    {"name": "string"},
    {"on_hand": "integer"},
    handles={"inventory_received": {"quantity": "integer"}},
)
SUPPLIER = _descriptor("factory.supplier.v1", {"name": "string"}, {"approved": "boolean"})
PURCHASE_ORDER = _descriptor(
    "factory.purchase_order.v1",
    {"number": "string", "quantity": "integer"},
    {"received": "integer"},
    relations={"supplier": "factory.supplier.v1", "warehouse": "factory.warehouse.v1"},
    emits={"inventory_received": {"quantity": "integer"}},
)
WORK_ORDER = _descriptor(
    "factory.work_order.v1",
    {"number": "string", "quantity": "integer"},
    {"completed": "integer"},
    relations={"workstation": "factory.workstation.v1"},
    emits={"work_started": {"work_order": "string"}, "work_completed": {"work_order": "string"}},
)
QUALITY_CHECK = _descriptor(
    "factory.quality_check.v1",
    {"specification": "string"},
    {"passed": "boolean"},
    relations={"work_order": "factory.work_order.v1"},
)

DESCRIPTORS = (WORKSTATION, WAREHOUSE, SUPPLIER, PURCHASE_ORDER, WORK_ORDER, QUALITY_CHECK)
