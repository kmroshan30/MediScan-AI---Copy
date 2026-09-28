from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from modules import medicine_search
from modules.medicine_search import (
    clear_cache,
    condense,
    is_relevant,
    name_score,
    normalize_query,
    relevance_tokens,
    search_medicine,
    web_search_links,
)


def fake_get_json(responses: dict[str, Any]):
    """Return a stand-in for _get_json that replays canned payloads by URL."""

    def _get_json(url, params=None, timeout=medicine_search.DEFAULT_TIMEOUT):
        for key, payload in responses.items():
            if key in url:
                return payload(params) if callable(payload) else payload
        return None

    return _get_json


class NormalizeQueryTests(unittest.TestCase):
    def test_removes_dose_and_form_words(self):
        self.assertEqual(normalize_query("Dolo 650"), "Dolo")
        self.assertEqual(normalize_query("Paracetamol 500mg"), "Paracetamol")
        self.assertEqual(
            normalize_query("Amoxicillin 500 mg capsule"), "Amoxicillin"
        )
        self.assertEqual(normalize_query("ORS Powder"), "ORS")

    def test_keeps_the_name_when_there_is_nothing_else(self):
        self.assertEqual(normalize_query("Augmentin"), "Augmentin")

    def test_handles_empty_input(self):
        self.assertEqual(normalize_query(""), "")
        self.assertEqual(normalize_query(None), "")


class RelevanceTests(unittest.TestCase):
    def test_whole_word_match_is_required(self):
        # "dolo" must not match an article about "Dolophine".
        self.assertFalse(is_relevant("Dolo", "Dolophine", "an opioid medicine"))
        self.assertTrue(is_relevant("Dolo", "Dolo 650", "a paracetamol tablet"))

    def test_every_meaningful_word_must_match(self):
        self.assertTrue(
            is_relevant(
                "Amoxicillin clavulanate",
                "Amoxicillin and Clavulanate Potassium",
            )
        )
        self.assertFalse(is_relevant("Amoxicillin clavulanate", "Amoxicillin"))

    def test_dose_words_are_not_meaningful(self):
        self.assertEqual(relevance_tokens("Dolo 650 mg"), ["dolo"])
        self.assertEqual(relevance_tokens("tab"), [])


class NameScoreTests(unittest.TestCase):
    def test_exact_name_beats_a_combination_product(self):
        query = "Metformin"
        exact = name_score(query, "Metformin Hydrochloride", 3.0)
        combination = name_score(
            query, "Sitagliptin And Metformin Hydrochloride", 3.0
        )
        self.assertGreater(exact, combination)

    def test_unrelated_name_scores_zero(self):
        self.assertEqual(name_score("Metformin", "ZITUVIMET", 3.0), 0.0)

    def test_brand_weight_beats_generic_weight(self):
        self.assertGreater(
            name_score("Augmentin", "Augmentin", medicine_search._BRAND_WEIGHT),
            name_score("Augmentin", "Augmentin", medicine_search._GENERIC_WEIGHT),
        )


class CondenseTests(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(condense("Fever, mild pain"), "Fever, mild pain")

    def test_long_text_is_cut_at_a_bullet(self):
        text = " • ".join(f"fact number {index} about the medicine" for index in range(40))
        result = condense(text, 120)
        self.assertLessEqual(len(result), 130)
        self.assertTrue(result.endswith("•") or result.endswith("…"))

    def test_empty_text(self):
        self.assertEqual(condense(None), "")


class OpenFdaLookupTests(unittest.TestCase):
    def _label(self, brand: str, generic: str) -> dict[str, Any]:
        return {
            "openfda": {
                "brand_name": [brand],
                "generic_name": [generic],
                "substance_name": [generic],
                "route": ["ORAL"],
            },
            "dosage_form": ["TABLET"],
            "purpose": ["Pain reliever"],
            "indications_and_usage": ["Uses • relief from pain"],
            "warnings": ["Warnings • liver warning"],
            "brand_name": [brand],
        }

    def _payload(self, labels_for: dict[str, list]):
        """openFDA answers with {"results": [...]} keyed by the search string.

        The module searches with the lower-cased name, because the token that
        identifies a medicine is normalised before it is sent.
        """

        def responder(params):
            return {"results": labels_for.get(params["search"], [])}

        return fake_get_json({"drug/label.json": responder})

    def test_picks_the_closest_label(self):
        labels = {
            'openfda.generic_name:"metformin"': [
                self._label("ZITUVIMET", "SITAGLIPTIN AND METFORMIN HYDROCHLORIDE"),
                self._label("Metformin Hydrochloride", "METFORMIN HYDROCHLORIDE"),
            ]
        }
        with patch.object(medicine_search, "_get_json", self._payload(labels)):
            result = medicine_search.lookup_openfda("Metformin")
        self.assertEqual(result["generic_name"], "Metformin Hydrochloride")
        self.assertEqual(result["_matched_name"], "Metformin Hydrochloride")
        self.assertIn("liver warning", result["warnings"])

    def test_dose_words_do_not_break_the_search(self):
        labels = {
            'openfda.brand_name:"dolo"': [self._label("DOLO-650", "ACETAMINOPHEN")]
        }
        with patch.object(medicine_search, "_get_json", self._payload(labels)):
            result = medicine_search.lookup_openfda("Dolo 650")
        self.assertEqual(result["generic_name"], "Acetaminophen")

    def test_label_lists_are_flattened(self):
        labels = {'openfda.brand_name:"crocin"': [self._label("Crocin", "ACETAMINOPHEN")]}
        with patch.object(medicine_search, "_get_json", self._payload(labels)):
            result = medicine_search.lookup_openfda("Crocin")
        self.assertEqual(result["uses"], "Uses • relief from pain")
        self.assertNotIn("[", result["warnings"])

    def test_every_word_of_the_query_must_match(self):
        # A label about plain amoxicillin is not an answer for a search for
        # "amoxicillin clavulanate".
        labels = {
            'openfda.brand_name:"amoxicillin"': [
                self._label("Amoxicillin", "AMOXICILLIN")
            ]
        }
        with patch.object(medicine_search, "_get_json", self._payload(labels)):
            result = medicine_search.lookup_openfda("Amoxicillin clavulanate")
        self.assertEqual(result, {})

    def test_a_partial_hit_is_still_returned(self):
        # "Pan" is a whole word inside "Pan-Zyme-S", so it is reported as a
        # partial match (the page warns about it) rather than silently dropped.
        labels = {
            'openfda.brand_name:"pan"': [
                self._label("Pan-Zyme-S", "PANCREAS BALANCE")
            ]
        }
        with patch.object(medicine_search, "_get_json", self._payload(labels)):
            result = medicine_search.lookup_openfda("Pan 40")
        self.assertEqual(result["_matched_name"], "Pan-Zyme-S")


class WikipediaLookupTests(unittest.TestCase):
    def test_rejects_a_page_that_is_not_about_a_medicine(self):
        summary = {
            "title": "Dolo",
            "extract": "Sominé Dolo was a Malian doctor and politician.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Dolo"}},
            "thumbnail": {},
        }
        with patch.object(
            medicine_search,
            "_get_json",
            fake_get_json({"api/rest_v1/page/summary": summary}),
        ):
            result = medicine_search.lookup_wikipedia("Dolo")
        self.assertEqual(result, {})

    def test_accepts_a_medicine_article(self):
        summary = {
            "title": "Paracetamol",
            "extract": "Paracetamol is an analgesic and antipyretic medicine.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Paracetamol"}},
            "thumbnail": {"source": "https://example.org/paracetamol.png"},
        }
        with patch.object(
            medicine_search,
            "_get_json",
            fake_get_json({"api/rest_v1/page/summary": summary}),
        ):
            result = medicine_search.lookup_wikipedia("Paracetamol")
        self.assertEqual(result["title"], "Paracetamol")
        self.assertEqual(result["image"], "https://example.org/paracetamol.png")

    def test_an_article_about_another_drug_is_rejected(self):
        summary = {
            "title": "Methadone",
            "extract": "Methadone, sold as Dolophine, is a potent opioid.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Methadone"}},
            "thumbnail": {},
        }
        with patch.object(
            medicine_search,
            "_get_json",
            fake_get_json({"api/rest_v1/page/summary": summary}),
        ):
            self.assertEqual(medicine_search.lookup_wikipedia("Dolo"), {})


class SearchMedicineTests(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def test_returns_the_same_shape_when_everything_fails(self):
        with patch.object(medicine_search, "requests", None):
            result = search_medicine("Some Unknown Tablet")
        self.assertFalse(result["matched"])
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["display_name"], "Some Unknown Tablet")
        self.assertTrue(result["web_links"])
        # Every key must exist so the page can render without guarding.
        for key in ("uses", "warnings", "side_effects", "description", "confidence"):
            self.assertIn(key, result)

    def test_short_input_is_not_searched(self):
        with patch.object(medicine_search, "_get_json", fake_get_json({})):
            result = search_medicine("a")
        self.assertFalse(result["matched"])

    def test_merge_marks_a_fuzzy_hit_as_partial(self):
        merged = medicine_search._merge(
            {"_matched_name": "Pan-Zyme-S", "brand_names": ["Pan-Zyme-S"]},
            {},
            {},
            "Pan 40",
        )
        self.assertTrue(merged["matched"])
        self.assertEqual(merged["confidence"], "partial")

        exact = medicine_search._merge(
            {"_matched_name": "Pan 40", "brand_names": ["Pan 40"]}, {}, {}, "Pan 40"
        )
        self.assertEqual(exact["confidence"], "high")

    def test_uses_web_links_in_the_chosen_language(self):
        links = web_search_links("Dolo 650", language="te")
        google = next(link for link in links if "Google" in link["label"])
        self.assertIn("hl=te", google["url"])


if __name__ == "__main__":
    unittest.main()
