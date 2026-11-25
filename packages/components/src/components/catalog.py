"""Built-in component catalog used by environments and offline examples."""

from engine.versions import artifact_versions

from .factory import DESCRIPTORS as FACTORY_DESCRIPTORS
from .norvale import DESCRIPTORS as NORVALE_DESCRIPTORS
from .registry import VERSION, Registry
from .weekly_demand import DESCRIPTOR as WEEKLY_DEMAND

DESCRIPTORS = (*NORVALE_DESCRIPTORS, *FACTORY_DESCRIPTORS, WEEKLY_DEMAND)


def registry():
    """Return a fresh registry for the bundled components."""
    return Registry(list(DESCRIPTORS))


def manifest(instances, adapters=()):
    """Build a version-stamped manifest for ``registry().resolve``."""
    return {"version": VERSION, "versions": artifact_versions(), "adapters": list(adapters), "instances": instances}
