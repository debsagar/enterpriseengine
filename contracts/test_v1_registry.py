import json
import unittest

from components.registry import Registry, RegistryError


def descriptor(type_id, **declarations):
    return dict(
        version=1, type=type_id, instance_schema={}, state_schema={},
        handles={}, emits={}, parameters={}, policies={}, relations={},
        requires_adapters=[], provides_adapters=[], provenance="contracts/test_v1_registry.py",
    ) | declarations


def instance(instance_id, type_id, **values):
    return dict(id=instance_id, type=type_id, config={}, state={}, parameters={},
                policies={}, relations={}) | values


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.descriptors = [
            descriptor("station@1", emits={"reading": {"lot": "string"}},
                       provides_adapters=["measurements@1"]),
            descriptor("review@1", handles={"reading": {"lot": "string"}},
                       instance_schema={"team": "string"}, state_schema={"reviewed": "integer"},
                       parameters={"units": "string"}, policies={"limit": "number"},
                       relations={"station": "station@1"},
                       requires_adapters=["measurements@1", "records@1"]),
        ]
        self.reviewer = instance(
            "reviewer", "review@1", config={"team": "QA"}, state={"reviewed": 0},
            parameters={"units": "pH"}, relations={"station": "source"},
            policies={"limit": [
                {"effective_from": "2027-02-01", "effective_until": None, "value": 6},
                {"effective_from": "2027-01-01", "effective_until": "2027-02-01", "value": 5.5},
            ]},
        )
        self.manifest = dict(version=1, adapters=["records@1"],
                             instances=[instance("source", "station@1"), self.reviewer])

    def assert_manifest_error(self, path, message):
        with self.assertRaises(RegistryError) as caught:
            Registry(self.descriptors).resolve(self.manifest)
        self.assertEqual(caught.exception.path, path)
        self.assertIn(message, str(caught.exception))

    def test_valid_manifest_resolves(self):
        registry = Registry(self.descriptors)
        result = registry.resolve(json.loads(json.dumps(self.manifest)))
        self.assertEqual(list(result), ["source", "reviewer"])
        self.assertEqual(result["reviewer"]["instance"], self.reviewer)
        self.assertEqual(result["reviewer"]["descriptor"], self.descriptors[1])
        result["reviewer"]["descriptor"]["requires_adapters"].clear()
        self.assertEqual(registry.resolve(self.manifest)["reviewer"]["descriptor"], self.descriptors[1])

    def test_duplicate_instance_id(self):
        self.reviewer["id"] = "source"
        self.assert_manifest_error("manifest.instances[1].id", "duplicate instance ID")

    def test_duplicate_descriptor_id(self):
        with self.assertRaises(RegistryError) as caught:
            Registry(self.descriptors + [self.descriptors[0]])
        self.assertEqual(caught.exception.path, "descriptors[2].type")

    def test_dangling_relation(self):
        self.reviewer["relations"]["station"] = "missing"
        self.assert_manifest_error("manifest.instances[1].relations.station", "dangling relation")

    def test_wrong_relation_type(self):
        self.reviewer["relations"]["station"] = "reviewer"
        self.assert_manifest_error("manifest.instances[1].relations.station", "incompatible relation")

    def test_incompatible_event_schema(self):
        self.descriptors[1]["handles"]["reading"]["lot"] = "integer"
        self.assert_manifest_error(
            "descriptors[1].handles.reading",
            "incompatible payload schema with descriptors[0].emits.reading",
        )
        self.descriptors.reverse()
        self.assert_manifest_error(
            "descriptors[0].handles.reading",
            "incompatible payload schema with descriptors[1].emits.reading",
        )

    def test_missing_adapter(self):
        self.manifest["adapters"].clear()
        self.assert_manifest_error("descriptors[1].requires_adapters[1]", "missing adapter records@1")

    def test_overlapping_policy_intervals(self):
        self.reviewer["policies"]["limit"][1]["effective_until"] = "2027-02-02"
        self.assert_manifest_error("manifest.instances[1].policies.limit[0].effective_from",
                                   "overlapping policy intervals")

    def test_unknown_type(self):
        self.reviewer["type"] = "review@2"
        self.assert_manifest_error("manifest.instances[1].type", "unregistered component type")

    def test_unsupported_manifest_version(self):
        self.manifest["version"] = True
        self.assert_manifest_error("manifest.version", "unsupported format version")

    def test_invalid_state(self):
        self.reviewer["state"]["reviewed"] = True
        self.assert_manifest_error("manifest.instances[1].state.reviewed", "expected integer")

    def test_nonserializable_state(self):
        self.reviewer["state"]["reviewed"] = float("nan")
        self.assert_manifest_error("manifest.instances[1].state.reviewed", "expected finite number")
