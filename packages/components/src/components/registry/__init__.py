"""Version 1 component declarations and preflight composition checks."""

from copy import deepcopy
from datetime import date
from math import isfinite


VERSION = 1
JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


class RegistryError(ValueError):
    """A declaration or manifest is invalid at the given input path."""

    def __init__(self, path, message):
        self.path = path
        super().__init__(f"{path}: {message}")


def _json(value, path, active=None):
    if type(value) in (dict, list):
        if active is None:
            active = set()
        identity = id(value)
        if identity in active:
            raise RegistryError(path, "circular JSON reference")
        active.add(identity)
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise RegistryError(path, "expected string object key")
                _json(item, f"{path}.{key}", active)
        else:
            for index, item in enumerate(value):
                _json(item, f"{path}[{index}]", active)
        active.remove(identity)
    elif type(value) is float:
        if not isfinite(value):
            raise RegistryError(path, "expected finite number")
    elif type(value) not in (str, int, bool, type(None)):
        raise RegistryError(path, "expected JSON value")


def _name(value, path):
    if type(value) is not str or not value.strip():
        raise RegistryError(path, "expected nonempty string")


def _object(value, path, fields=None):
    if type(value) is not dict:
        raise RegistryError(path, "expected object")
    if fields is not None:
        for key in fields:
            if key not in value:
                raise RegistryError(f"{path}.{key}", "required field")
        for key in value:
            if key not in fields:
                raise RegistryError(f"{path}.{key}", "unknown field")


def _array(value, path):
    if type(value) is not list:
        raise RegistryError(path, "expected array")
    return enumerate(value)


def _version(value, path):
    if type(value) is not int or value != VERSION:
        raise RegistryError(path, "unsupported format version")


def _schema(schema, path):
    _object(schema, path)
    for field, kind in schema.items():
        if type(kind) is not str or kind not in JSON_TYPES:
            raise RegistryError(f"{path}.{field}", "unknown JSON field type")


def _typed(value, kind, path):
    expected = JSON_TYPES[kind]
    if type(value) not in (expected if isinstance(expected, tuple) else (expected,)):
        raise RegistryError(path, f"expected {kind}")


def _record(value, schema, path):
    _object(value, path, schema)
    for field, kind in schema.items():
        _typed(value[field], kind, f"{path}.{field}")


def _date(value, path):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    except (TypeError, ValueError):
        pass
    raise RegistryError(path, "expected YYYY-MM-DD date")


def _policies(policies, schema, path):
    _object(policies, path, schema)
    for name, kind in schema.items():
        intervals = []
        name_path = f"{path}.{name}"
        for index, entry in _array(policies[name], name_path):
            entry_path = f"{name_path}[{index}]"
            _object(entry, entry_path, ("effective_from", "effective_until", "value"))
            _typed(entry["value"], kind, f"{entry_path}.value")
            start = _date(entry["effective_from"], f"{entry_path}.effective_from")
            end = entry["effective_until"]
            if end is not None:
                end = _date(end, f"{entry_path}.effective_until")
                if end <= start:
                    raise RegistryError(f"{entry_path}.effective_until", "must follow effective_from")
            intervals.append((start, index, end))
        previous_end = None
        for position, (start, index, end) in enumerate(sorted(intervals)):
            if position and (previous_end is None or start < previous_end):
                raise RegistryError(f"{name_path}[{index}].effective_from", "overlapping policy intervals")
            previous_end = end


def _adapters(names, path):
    for index, name in _array(names, path):
        _name(name, f"{path}[{index}]")
    return set(names)


class Registry:
    """Index descriptors by type ID and resolve a manifest without starting a run."""

    def __init__(self, descriptors):
        self._types = {}
        for index, descriptor in _array(descriptors, "descriptors"):
            path = f"descriptors[{index}]"
            _json(descriptor, path)
            _object(descriptor, path, (
                "version", "type", "instance_schema", "state_schema", "handles",
                "emits", "parameters", "policies", "relations", "requires_adapters",
                "provides_adapters", "provenance",
            ))
            _version(descriptor["version"], f"{path}.version")
            type_id = descriptor["type"]
            _name(type_id, f"{path}.type")
            if type_id in self._types:
                raise RegistryError(f"{path}.type", "duplicate type ID")
            for field in ("instance_schema", "state_schema", "parameters", "policies"):
                _schema(descriptor[field], f"{path}.{field}")
            for field in ("handles", "emits"):
                _object(descriptor[field], f"{path}.{field}")
                for event, schema in descriptor[field].items():
                    _schema(schema, f"{path}.{field}.{event}")
            _object(descriptor["relations"], f"{path}.relations")
            for name, target_type in descriptor["relations"].items():
                _name(target_type, f"{path}.relations.{name}")
            for field in ("requires_adapters", "provides_adapters"):
                _adapters(descriptor[field], f"{path}.{field}")
            _name(descriptor["provenance"], f"{path}.provenance")
            self._types[type_id] = (deepcopy(descriptor), path)

    def catalog(self):
        """Export independent copies of all registered descriptors, sorted by type ID."""
        return [deepcopy(self._types[type_id][0]) for type_id in sorted(self._types)]

    def resolve(self, manifest):
        """Return an ordered-by-input mapping of instance IDs to declarations."""
        _json(manifest, "manifest")
        _object(manifest, "manifest", ("version", "instances", "adapters"))
        _version(manifest["version"], "manifest.version")
        provided = _adapters(manifest["adapters"], "manifest.adapters")
        resolved = {}
        events = {}
        for index, instance in _array(manifest["instances"], "manifest.instances"):
            path = f"manifest.instances[{index}]"
            _object(instance, path, ("id", "type", "config", "state", "parameters", "policies", "relations"))
            instance_id = instance["id"]
            _name(instance_id, f"{path}.id")
            if instance_id in resolved:
                raise RegistryError(f"{path}.id", "duplicate instance ID")
            type_id = instance["type"]
            _name(type_id, f"{path}.type")
            if type_id not in self._types:
                raise RegistryError(f"{path}.type", "unregistered component type")
            descriptor, descriptor_path = self._types[type_id]
            for field, schema in (("config", "instance_schema"), ("state", "state_schema"),
                                  ("parameters", "parameters")):
                _record(instance[field], descriptor[schema], f"{path}.{field}")
            _policies(instance["policies"], descriptor["policies"], f"{path}.policies")
            _object(instance["relations"], f"{path}.relations", descriptor["relations"])
            for direction in ("handles", "emits"):
                for name, schema in descriptor[direction].items():
                    event_path = f"{descriptor_path}.{direction}.{name}"
                    other_schema, other_path = events.setdefault(name, (schema, event_path))
                    if schema != other_schema:
                        raise RegistryError(event_path, f"incompatible payload schema with {other_path}")
            provided.update(descriptor["provides_adapters"])
            resolved[instance_id] = {"instance": instance, "descriptor": descriptor}

        for index, entry in enumerate(resolved.values()):
            path = f"manifest.instances[{index}]"
            instance, descriptor = entry["instance"], entry["descriptor"]
            for name, target in instance["relations"].items():
                relation_path = f"{path}.relations.{name}"
                _name(target, relation_path)
                if target not in resolved:
                    raise RegistryError(relation_path, "dangling relation")
                if resolved[target]["instance"]["type"] != descriptor["relations"][name]:
                    raise RegistryError(relation_path, "incompatible relation target type")
            _, descriptor_path = self._types[instance["type"]]
            for offset, adapter in enumerate(descriptor["requires_adapters"]):
                if adapter not in provided:
                    raise RegistryError(
                        f"{descriptor_path}.requires_adapters[{offset}]", f"missing adapter {adapter}"
                    )
        return deepcopy(resolved)
