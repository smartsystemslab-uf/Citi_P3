import unittest

from bank_adk.sar_prompt import format_sar_case_facts


class SarPromptFormattingTests(unittest.TestCase):

    def test_compiled_case_facts_are_included_in_prompt(self):
        case_facts = {
            "names": ["Ava Patel", "Daniel Chen"],
            "amounts": [9200, 18400],
            "dates": ["2026-09-01", "2026-09-02"],
            "red_flags": [
                "repeated cash deposits",
                "possible structuring",
            ],
        }

        prompt = format_sar_case_facts(case_facts)

        self.assertIn("Ava Patel", prompt)
        self.assertIn("Daniel Chen", prompt)

        self.assertIn("9200", prompt)
        self.assertIn("18400", prompt)

        self.assertIn("2026-09-01", prompt)
        self.assertIn("2026-09-02", prompt)

        self.assertIn("repeated cash deposits", prompt)
        self.assertIn("possible structuring", prompt)


    def test_missing_fact_categories_are_handled(self):
        prompt = format_sar_case_facts({})

        self.assertIn("Names:", prompt)
        self.assertIn("Amounts:", prompt)
        self.assertIn("Dates:", prompt)
        self.assertIn("Red flags:", prompt)