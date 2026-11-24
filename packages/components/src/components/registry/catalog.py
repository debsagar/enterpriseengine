"""Repository component catalog for metadata consumers."""

from ..weekly_demand import DESCRIPTOR as WEEKLY_DEMAND
from . import Registry


def builtin_registry(extra_descriptors=()):
    """Build the catalog from shipped declarations and optional host types."""
    return Registry([WEEKLY_DEMAND, *extra_descriptors])
