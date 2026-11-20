"""Exercise the ordinary disposition check against retained native measurement fields."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from integrations.qms.rework import ReworkQuality
from native.company_rework import Rework


class ReworkDisposition(unittest.TestCase):
    def test_released_retest_requires_matching_original_measurements(self):
        archive = Path(__file__).resolve().parents[1] / 'integrations/qms/evidence/native-export.json'
        records = json.loads(archive.read_text())['Quality Inspection']
        saved = next(doc for doc in records.values() if doc.get('custom_retest_of'))
        parent = records[saved['custom_retest_of']]
        authority = dict(inspection=parent['name'], version=parent['modified'],
                         approver=parent['modified_by'], batch=parent['batch_no'])
        reader = Mock()
        reader.list.return_value = [{'name': saved['name'], 'creation': saved['creation']}]
        reader.get.side_effect = lambda entity, name: child if name == saved['name'] else parent
        quality = ReworkQuality(reader, None, saved['inspected_by'], parent['modified_by'])
        rework = Rework(None, quality, {}, qms_source='lab')
        key = 'repair'
        flow = SimpleNamespace(state={'rework': {key: {'authority': authority, 'retest': saved['name'],
                                                        'execution': 'mes:mrp.production:2'}}},
                               e=SimpleNamespace(date=lambda: saved['report_date']))
        lot = dict(batch='SALVAGE', rework_key=key, item=saved['item_code'], qty=7,
                   uom=saved['custom_lot_uom'], revision={'inspection_spec': saved['custom_spec_revision']})
        child = dict(saved, custom_lot_quantity=7, custom_rework_execution='mes:mrp.production:2',
                     custom_rework_output_batch='SALVAGE')
        self.assertIsNone(rework.disposition(flow, lot))  # Native archive ends at revocation.
        approved = dict(child, workflow_state='Released', modified_by=quality.approver_user)
        child = approved
        self.assertEqual(rework.disposition(flow, lot)['qms_source'], 'lab')
        for changes in ({'batch_no': 'another'}, {'inspected_by': 'another'}, {'readings': []},
                        {'readings': [dict(approved['readings'][0], reading_1='7.2')]},
                        {'readings': [dict(approved['readings'][0], max_value=7)]}):
            with self.subTest(fields=list(changes)):
                child = {**approved, **changes}
                self.assertIsNone(rework.disposition(flow, lot))
        child = approved
        parent = dict(parent)
        parent['modified'] = 'later version'
        self.assertIsNone(rework.disposition(flow, lot))
