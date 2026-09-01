#!/usr/bin/env python3
"""Extractor tests, grounded in the real corpus.

Every negative case below is a verbatim fragment from a transcribed LWIN call.
They are the point of the suite: `1003` (a dorm room) and `1042` (a real code)
are lexically identical, and only code-set membership separates them.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tencodes  # noqa: E402

CODES = {
    'id': 'test-set',
    'name': 'Test set',
    'ten': {
        '4':  {'meaning': 'Acknowledged', 'src': 's1', 'common': True},
        '8':  {'meaning': 'In service', 'src': 's1'},
        '15': {'meaning': 'Prisoner in custody', 'src': 's1'},
        '42': {'meaning': 'End of tour, off duty', 'src': 's3'},
    },
    'signal': {'20': {'meaning': 'Vehicle crash', 'src': 's2'}},
    'response': {'4': {'meaning': 'Scene secure, no further units needed', 'src': 's2'}},
}


def canon(text):
    norm, mentions = tencodes.extract(text, CODES)
    return norm, [(m.raw, m.canonical, m.kind, m.meaning, m.confidence)
                  for m in mentions]


class TestNegatives(unittest.TestCase):
    """Verbatim corpus fragments that must produce ZERO codes."""

    CASES = [
        'Can you be in route to Oxbow Hall, room 1003, for a welfare check',
        'back in the 1015 team and the mileage 46215 if there is',
        "I'm a 40-year-old male caller",
        'I got a suspicious 6627 Sullivan Road',
        'In 39, Kim Larkin, 39, occupied one time',
        'Transport 1010 15 from using a dramatic RCPD',
        '>> No more. >> ten more of that',
        'we down 28.5%',
        "That's my Bravo 8626 repeating, B862623",
    ]

    def test_no_codes_extracted(self):
        for text in self.CASES:
            with self.subTest(text=text):
                norm, mentions = canon(text)
                self.assertEqual(mentions, [], f'false positive in: {text}')

    def test_negative_text_is_returned_unchanged(self):
        for text in self.CASES:
            with self.subTest(text=text):
                norm, _ = canon(text)
                self.assertEqual(norm, text)


class TestSeparatedForms(unittest.TestCase):
    def test_hyphenated_code_resolves(self):
        _, m = canon('10-4, we are contracting.')
        self.assertEqual(m, [('10-4', '10-4', 'ten', 'Acknowledged', 'high')])

    def test_space_separated_code_normalizes_to_hyphen(self):
        norm, m = canon('One nine 10 4, same traffic')
        self.assertEqual(norm, 'One nine 10-4, same traffic')
        self.assertEqual(m[0][1], '10-4')

    def test_two_digit_wins_over_one_digit(self):
        _, m = canon('show me 10-42 for the night')
        self.assertEqual(m, [('10-42', '10-42', 'ten',
                              'End of tour, off duty', 'high')])

    def test_trailing_unit_number_is_not_absorbed(self):
        """'10-4-1-4-31' is 10-4 followed by unit 1-4-31, seen in the corpus."""
        _, m = canon('10-4-1-4-31')
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0][1], '10-4')

    def test_code_absent_from_the_set_is_recorded_unresolved(self):
        _, m = canon('send me a 10-84 out here')
        self.assertEqual(m, [('10-84', '10-84', 'ten', None, 'low')])

    def test_leading_zero_is_normalized(self):
        _, m = canon('show 10-04 please')
        self.assertEqual(m[0][1], '10-4')


class TestConcatenatedForms(unittest.TestCase):
    def test_known_code_is_split_and_marked_medium(self):
        norm, m = canon('Zachary, 43 is 1042.')
        self.assertEqual(norm, 'Zachary, 43 is 10-42.')
        self.assertEqual(m, [('1042', '10-42', 'ten',
                              'End of tour, off duty', 'medium')])

    def test_unknown_number_is_left_alone(self):
        norm, m = canon('It was 1003.')
        self.assertEqual(norm, 'It was 1003.')
        self.assertEqual(m, [])

    def test_address_word_suppresses_the_split(self):
        norm, m = canon('welfare check, room 1015, apartment D')
        self.assertEqual(norm, 'welfare check, room 1015, apartment D')
        self.assertEqual(m, [])

    def test_three_digits_after_ten_are_not_a_code(self):
        norm, m = canon('dispatching 10100 too rapidly')
        self.assertEqual(m, [])


class TestSpelledForms(unittest.TestCase):
    def test_ten_four(self):
        norm, m = canon('One nine ten four, same traffic')
        self.assertEqual(norm, 'One nine 10-4, same traffic')
        self.assertEqual(m[0][1], '10-4')

    def test_hyphenated_word_form(self):
        norm, _ = canon('the first ten-fifteen')
        self.assertEqual(norm, 'the first 10-15')

    def test_plural_form(self):
        norm, _ = canon('a couple of ten-fours')
        self.assertEqual(norm, 'a couple of 10-4')

    def test_oh_infix(self):
        norm, _ = canon('give me a ten oh four')
        self.assertEqual(norm, 'give me a 10-4')

    def test_compound_tens_and_units(self):
        norm, _ = canon('put me ten forty-two')
        self.assertEqual(norm, 'put me 10-42')

    def test_non_number_word_is_not_converted(self):
        for text in ('ten more', 'ten point five', 'ten minutes ago'):
            with self.subTest(text=text):
                norm, m = canon(text)
                self.assertEqual(norm, text)
                self.assertEqual(m, [])


class TestSignalAndResponse(unittest.TestCase):
    def test_signal_resolves(self):
        _, m = canon('possibly going to be a signal 20 if you could notify')
        self.assertEqual(m, [('signal 20', 'signal 20', 'signal',
                              'Vehicle crash', 'high')])

    def test_unknown_signal_is_recorded_unresolved(self):
        _, m = canon('His Signal 31 girlfriend is on scene')
        self.assertEqual(m, [('Signal 31', 'signal 31', 'signal', None, 'low')])

    def test_response_code_resolves(self):
        _, m = canon('backup 406, please, code 4, please.')
        self.assertEqual(m, [('code 4', 'code 4', 'response',
                              'Scene secure, no further units needed', 'high')])

    def test_unknown_response_code_is_recorded_unresolved(self):
        _, m = canon('respond code 1, lift assist')
        self.assertEqual(m, [('code 1', 'code 1', 'response', None, 'low')])


class TestOffsets(unittest.TestCase):
    def test_offsets_index_into_the_normalized_text(self):
        norm, mentions = tencodes.extract('Zachary, 43 is 1042.', CODES)
        m = mentions[0]
        self.assertEqual(norm[m.off_start:m.off_end], '10-42')

    def test_offsets_are_correct_after_a_spelled_form_shortens_the_text(self):
        norm, mentions = tencodes.extract('One nine ten four, same traffic', CODES)
        m = mentions[0]
        self.assertEqual(norm[m.off_start:m.off_end], '10-4')

    def test_multiple_mentions_have_distinct_increasing_offsets(self):
        norm, mentions = tencodes.extract('10-8, 10-42, have a good one', CODES)
        self.assertEqual(len(mentions), 2)
        self.assertLess(mentions[0].off_start, mentions[1].off_start)
        for m in mentions:
            self.assertEqual(norm[m.off_start:m.off_end], m.canonical)


class TestNonBmpOffsets(unittest.TestCase):
    """Offsets must stay valid when the text contains non-BMP characters.

    These offsets cross into TypeScript (utils/tencodeSegments.ts), which
    slices by UTF-16 code unit while Python indexes by code point. This side
    pins the Python contract; utils/tencodeSegments.test.ts pins the consumer.
    """

    def test_offsets_index_code_points_not_utf16_units(self):
        norm, mentions = tencodes.extract('\U0001F600 call 1042.', CODES)
        self.assertEqual(norm, '\U0001F600 call 10-42.')
        m = mentions[0]
        self.assertEqual(norm[m.off_start:m.off_end], '10-42')

    def test_offset_differs_from_the_utf16_index(self):
        """Proves the case actually exercises the hazard.

        If the emoji were one UTF-16 unit, code-point and code-unit indexes
        would agree and the test above would pass either way.
        """
        norm, mentions = tencodes.extract('\U0001F600 call 1042.', CODES)
        m = mentions[0]
        utf16_index = len(norm[:m.off_start].encode('utf-16-le')) // 2
        self.assertNotEqual(utf16_index, m.off_start)

    def test_multiple_non_bmp_characters(self):
        norm, mentions = tencodes.extract(
            '\U0001F600\U0001F692 radio 1042 clear', CODES)
        m = mentions[0]
        self.assertEqual(norm[m.off_start:m.off_end], '10-42')

    def test_non_bmp_after_the_code(self):
        norm, mentions = tencodes.extract('1042 \U0001F600', CODES)
        m = mentions[0]
        self.assertEqual(norm[m.off_start:m.off_end], '10-42')


class TestCodesText(unittest.TestCase):
    def test_includes_raw_canonical_and_meaning(self):
        _, mentions = tencodes.extract('Zachary, 43 is 1042.', CODES)
        blob = tencodes.codes_text(mentions)
        for token in ('1042', '10-42', 'End of tour'):
            self.assertIn(token, blob)

    def test_unresolved_code_still_contributes_raw_and_canonical(self):
        _, mentions = tencodes.extract('send me a 10-84', CODES)
        blob = tencodes.codes_text(mentions)
        self.assertIn('10-84', blob)

    def test_empty_for_no_mentions(self):
        self.assertEqual(tencodes.codes_text([]), '')


class TestPurity(unittest.TestCase):
    def test_normalized_text_is_always_returned_even_with_no_codes(self):
        norm, m = tencodes.extract('nothing to see here', CODES)
        self.assertEqual(norm, 'nothing to see here')
        self.assertEqual(m, [])

    def test_empty_input(self):
        self.assertEqual(tencodes.extract('', CODES), ('', []))


if __name__ == '__main__':
    unittest.main()
