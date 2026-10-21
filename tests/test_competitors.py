"""Check evidence, relation context and conservative identity resolution."""

import unittest

from azalea_edgar.competitors import extract_competitors


DIRECTORY = [
    {"name": "Apex Launch Corp /DE/", "ticker": "APEX", "cik": "0000000001"},
    {"name": "Beacon Space Inc.", "ticker": "BCN", "cik": "0000000002"},
    {"name": "Beacon Space Inc.", "ticker": "BCN.B", "cik": "0000000002"},
]


class CompetitorTests(unittest.TestCase):
    def test_contexts_and_joint_venture_owners_remain_distinct(self):
        text = ("Competition\n\nWe have several sources of competition:\n\n"
                "•companies providing launch vehicles to deliver satellites, such as Apex Launch, "
                "Orbit Alliance (a joint venture between Parent One and Parent Two), Beacon Space, "
                "and established Russian, Indian, Chinese, European and Japanese launch providers;\n\n"
                "•companies providing spacecraft components in the commercial marketplace, such as "
                "Beacon Space and Cedar Systems.\n\nIntellectual Property\n\nOur suppliers include Wrong Company.")
        result = extract_competitors(text, DIRECTORY)
        names = [c["name"] for c in result["candidates"]]
        self.assertEqual(names, ["Apex Launch", "Orbit Alliance", "Beacon Space", "Cedar Systems"])
        self.assertEqual(result["summary"], {"candidate_count": 4, "context_count": 2, "matched_count": 2})
        beacon = result["candidates"][2]
        self.assertEqual(beacon["group_ids"], ["g1", "g2"])
        self.assertEqual(beacon["identity"]["matches"][0]["tickers"], ["BCN", "BCN.B"])
        self.assertEqual(result["groups"][0]["label"], "Launch vehicles")
        for group in result["groups"]:
            self.assertEqual(text[group["start"]:group["end"]], group["quote"])

    def test_customers_and_performance_peers_are_not_competitors(self):
        text = ("Customers\n\nOur customers include Apex Launch and Beacon Space.\n\n"
                "Our shareholder return peer group includes Apex Launch and Beacon Space.")
        result = extract_competitors(text, DIRECTORY)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["status"], "not_found")

    def test_general_competition_is_an_empty_disclosure_not_no_competitors(self):
        text = "Competition\n\nWe compete on price and quality. Our competitors include large companies and international providers."
        result = extract_competitors(text, DIRECTORY)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["status"], "no_named_competitors")

    def test_explicit_statement_without_a_heading_and_legal_commas(self):
        text = "Our principal competitors include Apex Launch Corp., Beacon Space, Inc. and Cedar Systems."
        result = extract_competitors(text, DIRECTORY)
        self.assertEqual([c["name"] for c in result["candidates"]],
                         ["Apex Launch Corp", "Beacon Space, Inc", "Cedar Systems"])
        self.assertEqual(result["summary"]["matched_count"], 2)

    def test_brand_names_are_not_fuzzily_promoted_to_parent_issuers(self):
        result = extract_competitors("Competition\n\nOur competitors include Apex and Beacon Space.", DIRECTORY)
        self.assertEqual(result["candidates"][0]["identity"]["status"], "unresolved")
        self.assertEqual(result["candidates"][1]["identity"]["status"], "directory_match")

    def test_ambiguous_names_remain_unresolved_and_self_mentions_are_removed(self):
        directory = DIRECTORY + [{"name": "Apex Launch Ltd", "ticker": "AX2", "cik": "0000000003"}]
        result = extract_competitors("Our competitors include Apex Launch and Beacon Space.", directory, "Beacon Space Inc.")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["identity"]["status"], "ambiguous")

    def test_unicode_offsets_can_highlight_the_exact_browser_text(self):
        text = "Research 🚀\n\nCompetition\n\nOur competitors include Apex Launch and Beacon Space."
        group = extract_competitors(text, DIRECTORY)["groups"][0]
        encoded = text.encode("utf-16-le")
        self.assertEqual(encoded[group["start_utf16"] * 2:group["end_utf16"] * 2].decode("utf-16-le"), group["quote"])

    def test_absent_directory_keeps_disclosed_names_without_claiming_private_status(self):
        result = extract_competitors("We compete with Apex Launch and Beacon Space.")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertTrue(all(c["identity"]["status"] == "unresolved" for c in result["candidates"]))

    def test_market_competition_statement_with_semicolons_and_repeated_risk_text(self):
        statement = ("We face intense competition in the semiconductor memory and storage markets from a number of companies, "
                     "including Apex Launch Co., Ltd.; Beacon Space Inc.; and Cedar Systems, Inc. (\"CEDAR\"). "
                     "Our competitors may use aggressive pricing to obtain market share.")
        result = extract_competitors(statement + "\n\n" + statement, DIRECTORY)
        self.assertEqual([c["name"] for c in result["candidates"]],
                         ["Apex Launch Co., Ltd", "Beacon Space Inc", "Cedar Systems, Inc"])
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["label"], "Semiconductor memory and storage")

    def test_negative_inclusion_is_not_a_positive_competitor_relation(self):
        result = extract_competitors("Competition\n\nOur competitors do not include Apex Launch.", DIRECTORY)
        self.assertEqual(result["candidates"], [])

    def test_competition_heading_does_not_make_customers_or_suppliers_competitors(self):
        for relation in ("customers", "suppliers", "partners", "clients"):
            with self.subTest(relation=relation):
                text = f"Competition\n\nOur {relation} include Customer Alpha and Customer Beta."
                self.assertEqual(extract_competitors(text)["candidates"], [])

    def test_explicit_competition_does_not_jump_to_a_later_unrelated_list(self):
        for rival in ("Rival Alpha", "Rival Alpha Inc."):
            for relation in ("customers", "suppliers", "partners"):
                with self.subTest(rival=rival, relation=relation):
                    text = f"We compete with {rival}. Our {relation} include Other Beta and Other Gamma."
                    result = extract_competitors(text)
                    self.assertEqual([c["name"] for c in result["candidates"]], [rival.rstrip(".")])

    def test_business_list_keeps_the_prior_competition_assertion_as_evidence(self):
        frame = "We believe our main sources of competition fall into two categories:"
        text = f"Competition\n\n{frame}\n\n•companies providing launch services, such as Rival Alpha and Rival Beta."
        group = extract_competitors(text)["groups"][0]
        self.assertEqual(group["relation_basis"], "framed_competition_list")
        self.assertEqual(group["relation_text"], frame)

    def test_a_company_list_in_a_competition_section_needs_a_relation_assertion(self):
        text = "Competition\n\nCompanies providing services include Other Alpha and Other Beta."
        self.assertEqual(extract_competitors(text)["candidates"], [])

    def test_later_direct_predicate_is_checked_without_selecting_unrelated_mentions(self):
        text = ("Competition\nOur customers require reliable service. "
                "We compete with existing firms. For example, we also face competition from large healthcare IT providers "
                "such as Apex Medical, lowerbrand, Inc. and Beacon Health. Our partners include Partner Gamma.")
        result = extract_competitors(text)
        self.assertEqual([c["name"] for c in result["candidates"]],
                         ["Apex Medical", "lowerbrand, Inc", "Beacon Health"])
        group = result["groups"][0]
        self.assertEqual(group["label"], "Large healthcare IT providers")
        self.assertEqual(group["relation_basis"], "direct_statement")
        self.assertNotIn("Our customers", group["quote"])
        self.assertNotIn("Our partners", group["quote"])


if __name__ == "__main__":
    unittest.main()
