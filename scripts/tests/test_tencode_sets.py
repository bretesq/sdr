#!/usr/bin/env python3
"""Integrity and resolution tests for the 10-code data in data/tencodes/.

These are what make "sourced properly" enforceable rather than aspirational:
an expansion with no traceable source, a resolver rule pointing at a set that
does not exist, or a cyclic `extends` chain all fail here rather than shipping
a confident wrong meaning into a transcript.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tencode_sets  # noqa: E402


class TestDataIntegrity(unittest.TestCase):
    def test_every_entry_has_a_resolvable_source(self):
        for set_id in tencode_sets.all_set_ids():
            raw = tencode_sets.load_set(set_id)
            refs = {s['ref'] for s in raw.get('sources', [])}
            for table in ('ten', 'signal', 'response'):
                for code, entry in raw.get(table, {}).items():
                    with self.subTest(set=set_id, table=table, code=code):
                        self.assertIn('meaning', entry)
                        self.assertIn('src', entry)
                        self.assertIn(entry['src'], refs)

    def test_every_index_rule_names_an_existing_set(self):
        known = set(tencode_sets.all_set_ids())
        for rule in tencode_sets.load_index():
            with self.subTest(rule=rule):
                self.assertIn(rule['set'], known)

    def test_extends_chains_terminate_and_do_not_cycle(self):
        for set_id in tencode_sets.all_set_ids():
            with self.subTest(set=set_id):
                chain = tencode_sets.chain_of(set_id)
                self.assertEqual(len(chain), len(set(chain)))

    def test_index_has_a_catch_all_rule(self):
        rules = tencode_sets.load_index()
        self.assertEqual(rules[-1]['cat'], '*')
        self.assertEqual(rules[-1]['tag'], '*')


class TestResolution(unittest.TestCase):
    def test_brpd_law_resolves_to_the_brpd_set(self):
        got = tencode_sets.resolve_set_id(
            'East Baton Rouge Parish (17) - Baton Rouge Police', 'Law Dispatch')
        self.assertEqual(got, 'la-brpd-law')

    def test_fire_resolves_to_fire_not_law(self):
        got = tencode_sets.resolve_set_id(
            'East Baton Rouge Parish (17) - Fire/EMS', 'Fire Dispatch')
        self.assertEqual(got, 'la-generic-fire')

    def test_unknown_parish_falls_back_to_generic_law(self):
        got = tencode_sets.resolve_set_id(
            'Pointe Coupee Parish (39) - Public Safety', 'Law Dispatch')
        self.assertEqual(got, 'la-generic-law')

    def test_missing_metadata_falls_back_to_generic_law(self):
        self.assertEqual(tencode_sets.resolve_set_id(None, None), 'la-generic-law')

    def test_empty_agency_set_inherits_the_generic_codes(self):
        resolved = tencode_sets.resolve('la-brpd-law')
        self.assertEqual(resolved['ten']['4']['meaning'], 'Acknowledged')

    def test_fire_does_not_inherit_police_ten_codes(self):
        resolved = tencode_sets.resolve('la-generic-fire')
        self.assertNotIn('15', resolved['ten'])

    def test_response_codes_are_shared_across_disciplines(self):
        for set_id in ('la-generic-law', 'la-generic-fire', 'la-generic-ems'):
            with self.subTest(set=set_id):
                resolved = tencode_sets.resolve(set_id)
                self.assertEqual(resolved['response']['4']['meaning'],
                                 'Scene secure, no further units needed')

    def test_fire_inherits_the_universal_ten_codes(self):
        """10-4 is universal. Leaving it out of the fire chain left 27 corpus
        occurrences unresolved, because 1,076 calls are Fire/EMS."""
        resolved = tencode_sets.resolve('la-generic-fire')
        for code in ('4', '7', '8', '9', '20', '97'):
            with self.subTest(code=code):
                self.assertIn(code, resolved['ten'])

    def test_law_only_codes_do_not_leak_into_fire(self):
        resolved = tencode_sets.resolve('la-generic-fire')
        for code in ('6', '15', '19', '42'):
            with self.subTest(code=code):
                self.assertNotIn(code, resolved['ten'])


class TestRev(unittest.TestCase):
    def test_rev_is_stable_for_the_same_input(self):
        a = tencode_sets.resolve('la-generic-law')
        b = tencode_sets.resolve('la-generic-law')
        self.assertEqual(tencode_sets.set_rev(a, 'v1'),
                         tencode_sets.set_rev(b, 'v1'))

    def test_rev_changes_when_a_meaning_changes(self):
        a = tencode_sets.resolve('la-generic-law')
        b = tencode_sets.resolve('la-generic-law')
        b['ten']['4'] = dict(b['ten']['4'], meaning='Something else')
        self.assertNotEqual(tencode_sets.set_rev(a, 'v1'),
                            tencode_sets.set_rev(b, 'v1'))

    def test_rev_changes_when_the_extractor_version_changes(self):
        a = tencode_sets.resolve('la-generic-law')
        self.assertNotEqual(tencode_sets.set_rev(a, 'v1'),
                            tencode_sets.set_rev(a, 'v2'))

    def test_rev_ignores_the_common_flag(self):
        a = tencode_sets.resolve('la-generic-law')
        b = tencode_sets.resolve('la-generic-law')
        b['ten']['4'] = dict(b['ten']['4'], common=False)
        self.assertEqual(tencode_sets.set_rev(a, 'v1'),
                         tencode_sets.set_rev(b, 'v1'))


if __name__ == '__main__':
    unittest.main()
