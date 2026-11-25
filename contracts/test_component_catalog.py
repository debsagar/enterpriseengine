"""Offline checks for the bundled reusable component declarations."""

import unittest

from components.catalog import DESCRIPTORS, manifest, registry


class ComponentCatalogTests(unittest.TestCase):
    def test_catalog_resolves_a_small_factory(self):
        instances = [
            self.instance(
                "workstation", "factory.workstation.v1", {"name": "Mixer", "capacity": 1}, {"available": True}
            ),
            self.instance("warehouse", "factory.warehouse.v1", {"name": "Finished goods"}, {"on_hand": 0}),
            self.instance("supplier", "factory.supplier.v1", {"name": "Acme"}, {"approved": True}),
            self.instance(
                "purchase",
                "factory.purchase_order.v1",
                {"number": "PO-1", "quantity": 8},
                {"received": 0},
                {"supplier": "supplier", "warehouse": "warehouse"},
            ),
            self.instance(
                "work",
                "factory.work_order.v1",
                {"number": "WO-1", "quantity": 8},
                {"completed": 0},
                {"workstation": "workstation"},
            ),
            self.instance(
                "quality",
                "factory.quality_check.v1",
                {"specification": "pH"},
                {"passed": False},
                {"work_order": "work"},
            ),
        ]
        resolved = registry().resolve(manifest(instances))
        self.assertEqual(set(resolved), {"workstation", "warehouse", "supplier", "purchase", "work", "quality"})

    def test_catalog_includes_norvale_and_factory_types(self):
        self.assertIn("norvale.product.v1", {descriptor["type"] for descriptor in DESCRIPTORS})
        self.assertIn("factory.quality_check.v1", {descriptor["type"] for descriptor in DESCRIPTORS})

    @staticmethod
    def instance(instance_id, type_id, config, state, relations=None):
        return {
            "id": instance_id,
            "type": type_id,
            "config": config,
            "state": state,
            "parameters": {},
            "policies": {},
            "relations": relations or {},
        }
