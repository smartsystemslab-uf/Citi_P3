import json
import unittest

from bank_adk.tools import aml_tools


class VerifyIdentityDocumentTests(unittest.TestCase):
    def score(self, **overrides):
        fields = {
            "submitted_name": "Ava Patel",
            "submitted_dob": "2000-02-29",
            "submitted_id_number": "FL-001234",
            "reference_name": "Ava Patel",
            "reference_dob": "2000-02-29",
            "reference_id_number": "FL-001234",
        }
        fields.update(overrides)
        return aml_tools.verify_identity_document(**fields)

    def test_exact_match_returns_full_score_and_serializable_breakdown(self):
        result = self.score()
        self.assertEqual(result, {
            "status": "success",
            "match_confidence": 1.0,
            "field_scores": {"name": 1.0, "dob": 1.0, "id_number": 1.0},
        })
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_all_fields_mismatch_returns_zero(self):
        result = self.score(
            submitted_name="X", submitted_dob="1999-01-01", submitted_id_number="ZZ9"
        )
        self.assertEqual(result["match_confidence"], 0.0)
        self.assertEqual(result["field_scores"], {"name": 0.0, "dob": 0.0, "id_number": 0.0})

    def test_individual_mismatches_remove_only_that_fields_weight(self):
        cases = [
            ("submitted_name", "X", "name", 0.7),
            ("submitted_dob", "1999-01-01", "dob", 0.7),
            ("submitted_id_number", "ZZ9", "id_number", 0.6),
        ]
        for argument, value, field, expected in cases:
            with self.subTest(field=field):
                result = self.score(**{argument: value})
                self.assertEqual(result["match_confidence"], expected)
                self.assertEqual(result["field_scores"][field], 0.0)

    def test_single_matching_field_keeps_its_original_weight(self):
        for name, dob, id_number, expected in [
            ("Ava Patel", "1999-01-01", "ZZ9", 0.3),
            ("X", "2000-02-29", "ZZ9", 0.3),
            ("X", "1999-01-01", "FL-001234", 0.4),
        ]:
            with self.subTest(expected=expected, name=name):
                result = self.score(
                    submitted_name=name, submitted_dob=dob, submitted_id_number=id_number
                )
                self.assertEqual(result["match_confidence"], expected)

    def test_name_typo_earns_partial_credit(self):
        result = self.score(submitted_name="Eva Patel")
        self.assertEqual(result["field_scores"]["name"], 0.8889)
        self.assertEqual(result["match_confidence"], 0.9667)

    def test_name_edits_have_known_symmetric_scores(self):
        for left, right, expected in [
            ("Anna", "Ana", 0.75),
            ("Anna", "Anan", 0.5),
            ("a", "bc", 0.0),
            ("ab", "cab", 0.6667),
        ]:
            with self.subTest(left=left, right=right):
                forward = self.score(submitted_name=left, reference_name=right)
                backward = self.score(submitted_name=right, reference_name=left)
                self.assertEqual(forward, backward)
                self.assertEqual(forward["field_scores"]["name"], expected)

    def test_name_normalizes_case_whitespace_and_equivalent_unicode(self):
        for submitted, reference in [
            ("  AVA\t  PATEL\n", "Ava Patel"),
            ("JOSE\u0301   A\u0301LVAREZ", "Jos\u00e9 \u00c1lvarez"),
            ("\u674e \u660e", "\u674e \u660e"),
        ]:
            with self.subTest(submitted=submitted):
                self.assertEqual(self.score(
                    submitted_name=submitted, reference_name=reference
                )["match_confidence"], 1.0)

    def test_name_does_not_silently_remove_accents_punctuation_or_reorder(self):
        for submitted, reference in [
            ("Jose Alvarez", "Jos\u00e9 Alvarez"),
            ("Ava-Patel", "Ava Patel"),
            ("Patel Ava", "Ava Patel"),
        ]:
            with self.subTest(submitted=submitted):
                self.assertLess(self.score(
                    submitted_name=submitted, reference_name=reference
                )["field_scores"]["name"], 1.0)

    def test_dob_accepts_exact_calendar_date_and_surrounding_whitespace(self):
        self.assertEqual(self.score(submitted_dob=" 2000-02-29\t")["match_confidence"], 1.0)
        # Matching-only scope: no time-dependent age or future-date policy.
        self.assertEqual(self.score(
            submitted_dob="2999-01-01", reference_dob="2999-01-01"
        )["field_scores"]["dob"], 1.0)

    def test_invalid_or_noncanonical_dob_never_matches_even_itself(self):
        for invalid in [
            "", " ", "not-a-date", "2001-02-29", "2000-02-30", "0000-01-01",
            "2000-13-01", "2000-2-29", "02/29/2000", "20000229", "2000-W09-2",
            "2000-02-29T00:00:00", None, 20000229,
        ]:
            for overrides in [
                {"submitted_dob": invalid},
                {"reference_dob": invalid},
                {"submitted_dob": invalid, "reference_dob": invalid},
            ]:
                with self.subTest(overrides=overrides):
                    result = self.score(**overrides)
                    self.assertEqual(result["field_scores"]["dob"], 0.0)
                    self.assertEqual(result["match_confidence"], 0.7)

    def test_id_trims_outer_whitespace_but_preserves_every_internal_character(self):
        self.assertEqual(self.score(submitted_id_number=" FL-001234\t")["match_confidence"], 1.0)
        for mismatch in ["fl-001234", "FL001234", "FL-1234", "FL-00 1234", "FL-001235"]:
            with self.subTest(mismatch=mismatch):
                result = self.score(submitted_id_number=mismatch)
                self.assertEqual(result["field_scores"]["id_number"], 0.0)
                self.assertEqual(result["match_confidence"], 0.6)

    def test_missing_or_nonstring_fields_never_match_even_each_other(self):
        for field, score_key, expected in [
            ("name", "name", 0.7), ("dob", "dob", 0.7), ("id_number", "id_number", 0.6)
        ]:
            for invalid in ["", " \t", None, 0, True, [], {}]:
                for side in ["submitted", "reference", "both"]:
                    overrides = {}
                    if side in ("submitted", "both"):
                        overrides[f"submitted_{field}"] = invalid
                    if side in ("reference", "both"):
                        overrides[f"reference_{field}"] = invalid
                    with self.subTest(field=field, invalid=invalid, side=side):
                        result = self.score(**overrides)
                        self.assertEqual(result["field_scores"][score_key], 0.0)
                        self.assertEqual(result["match_confidence"], expected)

    def test_all_missing_fields_score_zero(self):
        for empty in ["", None]:
            with self.subTest(empty=empty):
                result = aml_tools.verify_identity_document(*([empty] * 6))
                self.assertEqual(result["match_confidence"], 0.0)

    def test_names_need_letters_and_ids_need_alphanumeric_characters(self):
        for invalid in ["---", "...", "123"]:
            with self.subTest(name=invalid):
                self.assertEqual(self.score(
                    submitted_name=invalid, reference_name=invalid
                )["field_scores"]["name"], 0.0)
        for invalid in ["---", "..."]:
            with self.subTest(id_number=invalid):
                self.assertEqual(self.score(
                    submitted_id_number=invalid, reference_id_number=invalid
                )["field_scores"]["id_number"], 0.0)

    def test_score_is_bounded_and_consistent_with_breakdown(self):
        for name in ["", "Ava Patel", "Eva Patel", "A", "X", "Ava Patell"]:
            for dob in ["", "2000-02-29", "1999-01-01"]:
                for id_number in ["", "FL-001234", "ZZ9"]:
                    with self.subTest(name=name, dob=dob, id_number=id_number):
                        result = self.score(
                            submitted_name=name, submitted_dob=dob, submitted_id_number=id_number
                        )
                        fields = result["field_scores"]
                        self.assertEqual(result["match_confidence"], round(
                            0.3 * fields["name"] + 0.3 * fields["dob"] + 0.4 * fields["id_number"], 4
                        ))
                        for value in [result["match_confidence"], *fields.values()]:
                            self.assertGreaterEqual(value, 0.0)
                            self.assertLessEqual(value, 1.0)

    def test_result_exposes_only_scores_and_completion_status(self):
        result = self.score()
        self.assertEqual(set(result), {"status", "match_confidence", "field_scores"})
        self.assertEqual(set(result["field_scores"]), {"name", "dob", "id_number"})

    def test_registered_alongside_existing_aml_tools(self):
        self.assertIn(aml_tools.verify_identity_document, aml_tools.AML_TOOLS)
        self.assertIn(aml_tools.fetch_transaction_history, aml_tools.AML_TOOLS)
        self.assertIn(aml_tools.screen_against_sanctions, aml_tools.AML_TOOLS)

    def test_mismatched_name_returns_lower_confidence(self):
        result = aml_tools.verify_identity_document(
            submitted_name="Completely Different Person",
            submitted_dob="2000-02-29",
            submitted_id_number="FL-001234",
            reference_name="Ava Patel",
            reference_dob="2000-02-29",
            reference_id_number="FL-001234",
        )

        self.assertLess(result["match_confidence"], 0.8)


    def test_mismatched_dob_returns_lower_confidence(self):
        result = aml_tools.verify_identity_document(
            submitted_name="Ava Patel",
            submitted_dob="1990-01-01",
            submitted_id_number="FL-001234",
            reference_name="Ava Patel",
            reference_dob="2000-02-29",
            reference_id_number="FL-001234",
        )

        self.assertLess(result["match_confidence"], 0.8)
