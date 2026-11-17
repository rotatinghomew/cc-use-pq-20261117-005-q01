import unittest

from story_patterns.catalog.validate import digest_json
from story_patterns.errors import ConflictError, NotFoundError, ValidationError
from tests.support import AppCase, batch, decision, story


def alias(story_tradition="coast", raw_name="Elder", canonical_id="elder-canonical",
          alias_id="alias-one", created_by="editor-one", canonical_name="Elder Brother"):
    return {
        "alias_id": alias_id,
        "tradition": story_tradition,
        "raw_name": raw_name,
        "canonical_id": canonical_id,
        "canonical_name": canonical_name,
        "rationale": "Synthetic mapping justified by parallel event order.",
        "created_by": created_by,
    }


def alternatives(tradition="mountain", names=("Oldest", "Middle", "Youngest"),
                 story_id="tale-b", revision=1):
    record = story(story_id, revision, tradition)
    for character, name in zip(record["characters"], names):
        character["name"] = name
    return record


class AliasRegistryTests(AppCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.import_story()

    def test_alias_record_is_attributed_and_replayable(self):
        result = self.app.record_character_alias(alias())
        self.assertEqual(result["status"], "recorded")
        listing = self.app.character_aliases()
        self.assertEqual(listing["active_mapping_count"], 1)
        stored = listing["aliases"][0]
        self.assertEqual(stored["rationale"], "Synthetic mapping justified by parallel event order.")
        self.assertEqual(stored["created_by"], "editor-one")

    def test_retry_is_idempotent_and_changed_content_conflicts(self):
        record = alias()
        self.assertEqual(self.app.record_character_alias(record)["status"], "recorded")
        self.assertEqual(self.app.record_character_alias(record)["status"], "already_recorded")
        changed = alias(canonical_id="someone-else")
        with self.assertRaises(ConflictError):
            self.app.record_character_alias(changed)
        self.assertEqual(self.app.character_aliases()["active_mapping_count"], 1)

    def test_same_raw_name_mapped_twice_in_one_tradition_conflicts(self):
        self.app.record_character_alias(alias())
        second = alias(canonical_id="father-canonical", alias_id="alias-two",
                       canonical_name="Father")
        with self.assertRaisesRegex(ConflictError, "already"):
            self.app.record_character_alias(second)

    def test_same_raw_name_in_a_different_tradition_is_independent(self):
        self.app.record_character_alias(alias())
        self.app.record_character_alias(alias(story_tradition="mountain", alias_id="alias-mount"))
        self.assertEqual(self.app.character_aliases()["active_mapping_count"], 2)

    def test_canonical_name_must_stay_unique_across_identifiers(self):
        self.app.record_character_alias(alias())
        clashing = alias(story_tradition="mountain", raw_name="Oldest",
                         canonical_id="other-id", alias_id="alias-two",
                         canonical_name="Elder Brother")
        with self.assertRaisesRegex(ConflictError, "canonical name"):
            self.app.record_character_alias(clashing)

    def test_same_canonical_identifier_cannot_be_renamed(self):
        self.app.record_character_alias(alias())
        renamed = alias(story_tradition="mountain", raw_name="Oldest",
                        alias_id="alias-two", canonical_name="First Born")
        with self.assertRaisesRegex(ConflictError, "different content"):
            self.app.record_character_alias(renamed)

    def test_alias_validation_rejects_blank_labels_and_missing_attribution(self):
        for field in ["alias_id", "tradition", "raw_name", "canonical_id", "canonical_name",
                      "rationale", "created_by"]:
            with self.subTest(field=field):
                record = alias()
                record[field] = " "
                with self.assertRaises(ValidationError):
                    self.app.record_character_alias(record)

    def test_revoke_keeps_the_original_mapping_in_history(self):
        self.app.record_character_alias(alias())
        result = self.app.revoke_character_alias({
            "alias_id": "alias-one", "revoked_by": "editor-two",
            "revocation_reason": "Name referred to a neighbour in the source commentary.",
        })
        self.assertEqual(result["status"], "revoked")
        listing = self.app.character_aliases()
        self.assertEqual(listing["active_mapping_count"], 0)
        self.assertEqual(listing["revoked_mapping_count"], 1)
        historical = listing["aliases"][0]
        self.assertEqual(historical["raw_name"], "Elder")
        self.assertEqual(historical["revoked_by"], "editor-two")
        self.assertTrue(historical["revocation_reason"])

    def test_revoke_is_idempotent_but_different_reasons_conflict(self):
        self.app.record_character_alias(alias())
        first = {"alias_id": "alias-one", "revoked_by": "editor-two", "revocation_reason": "wrong"}
        self.app.revoke_character_alias(first)
        self.assertEqual(self.app.revoke_character_alias(first)["status"], "already_revoked")
        with self.assertRaises(ConflictError):
            self.app.revoke_character_alias({**first, "revocation_reason": "different"})

    def test_revoked_name_can_be_mapped_again_as_a_new_attributed_record(self):
        self.app.record_character_alias(alias())
        self.app.revoke_character_alias({"alias_id": "alias-one", "revoked_by": "editor-two",
                                         "revocation_reason": "Provisional mapping withdrawn."})
        replacement = alias(canonical_id="elder-b", alias_id="alias-two",
                            canonical_name="Elder, revised")
        self.assertEqual(self.app.record_character_alias(replacement)["status"], "recorded")
        listing = self.app.character_aliases()
        self.assertEqual(listing["active_mapping_count"], 1)
        self.assertEqual([item["alias_id"] for item in listing["aliases"]],
                         ["alias-one", "alias-two"])

    def test_revoking_an_unknown_alias_is_a_domain_error(self):
        with self.assertRaises(NotFoundError):
            self.app.revoke_character_alias({"alias_id": "absent", "revoked_by": "editor-two",
                                             "revocation_reason": "nothing to revoke"})

    def test_listing_rejects_a_blank_tradition_filter(self):
        with self.assertRaises(ValidationError):
            self.app.character_aliases(" ")


class CharacterViewTests(AppCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.import_story(story("tale-a", tradition="coast"), "coast-batch")
        self.app.import_batch(batch([alternatives()], "mountain-batch"))

    def test_reimported_revision_does_not_inherit_old_aliases_or_decisions(self):
        self.import_story(story(revision=1), "revision-one")
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        self.add_votes(revision=1)
        revised = story(revision=2)
        revised["characters"][0]["name"] = "Elder (gatekeeper)"
        self.app.import_batch(batch([revised], "revision-two"))
        report = self.app.create_report()
        view = report["character_view"]
        rows = {(story_row["revision"], character["raw_name"]): character
                for story_row in view["stories"] if story_row["story_id"] == "tale-a"
                for character in story_row["characters"]}
        self.assertEqual(rows[(1, "Elder")]["analysis_id"], "canonical:elder")
        self.assertEqual(rows[(2, "Elder (gatekeeper)")]["analysis_id"],
                         "raw:coast:Elder (gatekeeper)")
        unmapped = {(item["revision"], item["raw_name"]) for item in view["unmapped"]
                    if item["story_id"] == "tale-a"}
        self.assertIn((2, "Elder (gatekeeper)"), unmapped)
        self.assertNotIn((1, "Elder"), unmapped)
        self.assertEqual(len(self.app.review.decisions_for("tale-a", 1)), 2)
        self.assertEqual(self.app.review.decisions_for("tale-a", 2), [])
        self.assertEqual([row["revision"] for row in report["story_results"]
                          if row["story_id"] == "tale-a"], [1, 2])

    def test_reimporting_the_same_revision_with_changed_names_is_a_conflict(self):
        self.import_story(story(revision=1), "revision-one")
        changed = story(revision=1)
        changed["characters"][0]["name"] = "Elder (revised)"
        with self.assertRaises(ConflictError):
            self.app.import_batch(batch([changed], "revision-one-again"))
        self.assertEqual(self.app.catalog.get_story("tale-a", 1)["characters"][0]["name"], "Elder")

    def test_unmapped_report_keeps_every_raw_name_distinct(self):
        report = self.app.create_report()
        names = [row["display_name"] for row in report["character_view"]["characters"]]
        self.assertIn("Elder", names)
        self.assertIn("Oldest", names)
        denominators = report["character_view"]["denominators"]
        self.assertEqual(denominators["raw_character_entries"], 6)
        self.assertEqual(denominators["aliased_character_entries"], 0)
        self.assertEqual(denominators["distinct_analysis_characters"], 6)

    def test_alias_merges_the_same_role_across_traditions(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        self.app.record_character_alias(alias(story_tradition="mountain", raw_name="Oldest",
                                              canonical_id="elder", alias_id="alias-mountain"))
        report = self.app.create_report()
        row = next(item for item in report["character_view"]["characters"]
                   if item["analysis_id"] == "canonical:elder")
        self.assertEqual(row["display_name"], "Elder Brother")
        self.assertEqual(row["story_revisions"], 2)
        self.assertEqual(row["events"], 2)
        self.assertEqual(sorted(row["raw_names"]), ["Elder", "Oldest"])
        self.assertEqual(sorted(row["traditions"]), ["coast", "mountain"])
        self.assertEqual(sorted(row["alias_ids"]), ["alias-mountain", "alias-one"])
        denominators = report["character_view"]["denominators"]
        self.assertEqual(denominators["aliased_character_entries"], 2)
        self.assertEqual(denominators["distinct_analysis_characters"], 5)

    def test_raw_names_and_decisions_are_not_rewritten_by_aliasing(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        self.app.record_decision(decision(annotator="reader-a"))
        before = self.app.catalog.get_story("tale-a", 1)
        self.app.create_report()
        after = self.app.catalog.get_story("tale-a", 1)
        self.assertEqual(before, after)
        self.assertEqual([item["name"] for item in after["characters"]], ["Elder", "Middle", "Young"])
        stored = self.app.review.decisions_for("tale-a", 1)[0]
        self.assertEqual(stored["evidence_event_ids"], ["first", "third"])

    def test_search_and_manifest_still_use_original_character_names(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        self.assertIn("Elder", [row["title"] for row in []] or
                      [item["name"] for item in self.app.catalog.get_story("tale-a", 1)["characters"]])
        self.assertEqual(self.app.search_stories(text="Oldest")[0]["story_id"], "tale-b")
        manifest = self.app.catalog_manifest()
        self.assertEqual(manifest["story_revision_count"], 2)

    def test_collision_between_two_roles_merged_into_one_canonical_is_reported(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="shared"))
        self.app.record_character_alias(alias(raw_name="Middle", canonical_id="shared",
                                              alias_id="alias-two"))
        report = self.app.create_report()
        collision = report["character_view"]["collisions"][0]
        self.assertEqual(collision["kind"], "merge_collision")
        self.assertEqual(collision["analysis_id"], "canonical:shared")
        self.assertEqual(collision["character_ids"], ["eldest", "middle"])
        self.assertEqual(report["character_view"]["denominators"]["collision_story_revisions"], 1)
        self.assertEqual(report["character_view"]["coverage"]["by_story_revision"]["with_collision"],
                         0.5)
        self.assertNotIn("canonical:shared",
                         [row["analysis_id"] for row in report["character_view"]["characters"]])

    def test_duplicate_raw_names_within_one_revision_are_flagged_not_merged(self):
        ambiguous = story("tale-c")
        ambiguous["characters"][1]["name"] = "Elder"
        self.app.import_batch(batch([ambiguous], "ambiguous-batch"))
        report = self.app.create_report()
        collision = next(item for item in report["character_view"]["collisions"]
                         if item["story_id"] == "tale-c")
        self.assertEqual(collision["kind"], "ambiguous_raw_name")
        self.assertEqual(collision["character_ids"], ["eldest", "middle"])
        self.assertNotIn("raw:tale-c:Elder",
                         [row["analysis_id"] for row in report["character_view"]["characters"]])
        self.assertEqual(report["character_view"]["denominators"]["collision_character_entries"], 2)

    def test_unmapped_names_are_listed_with_enough_context_to_resolve_them(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        report = self.app.create_report()
        unmapped = {(item["story_id"], item["raw_name"]) for item in report["character_view"]["unmapped"]}
        self.assertIn(("tale-b", "Oldest"), unmapped)
        self.assertNotIn(("tale-a", "Elder"), unmapped)
        self.assertEqual(len(unmapped), 5)

    def test_character_policy_is_frozen_into_report_identity(self):
        first = self.app.create_report()
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        second = self.app.create_report()
        self.assertNotEqual(first["report_id"], second["report_id"])
        self.assertNotEqual(first["character_view"]["policy_id"],
                            second["character_view"]["policy_id"])
        stored = self.app.stored_report(first["report_id"])
        self.assertEqual(stored["provenance"]["character_policy"]["mappings"], [])
        self.assertEqual(stored["provenance"]["character_policy_id"],
                         stored["character_view"]["policy_id"])

    def test_policy_only_freezes_mappings_relevant_to_the_reported_cohort(self):
        unrelated = alias(story_tradition="unused", raw_name="Elder", alias_id="alias-unused",
                          canonical_id="unused-elder", canonical_name="Unused Elder")
        self.app.record_character_alias(unrelated)
        self.app.record_character_alias(alias(story_tradition="coast", raw_name="Elder",
                                              alias_id="alias-coast", canonical_id="elder"))
        report = self.app.create_report("coast")
        self.assertEqual([item["tradition"] for item in
                          report["provenance"]["character_policy"]["mappings"]], ["coast"])

    def test_character_can_be_compared_within_each_tradition_separately(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        self.app.record_character_alias(alias(story_tradition="mountain", raw_name="Oldest",
                                              canonical_id="elder", alias_id="alias-mountain"))
        comparison = self.app.compare_traditions()
        character = next(row for row in comparison["canonical_characters"]["characters"]
                         if row["canonical_id"] == "elder")
        self.assertEqual([item["tradition"] for item in character["traditions"]],
                         ["coast", "mountain"])
        self.assertEqual([item["story_revisions"] for item in character["traditions"]], [1, 1])
        coastal_group = next(group for group in comparison["groups"]
                             if group["tradition"] == "coast")
        self.assertEqual(coastal_group["character_policy_id"],
                         digest_json({
                             "canonicals": [{"canonical_id": "elder",
                                             "canonical_name": "Elder Brother"}],
                             "mappings": [{"tradition": "coast", "raw_name": "Elder",
                                           "canonical_id": "elder", "alias_id": "alias-one"}],
                         }))

    def test_revocation_changes_new_reports_but_leaves_revision_text_alone(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        before = self.app.create_report()
        self.app.revoke_character_alias({"alias_id": "alias-one", "revoked_by": "editor-two",
                                         "revocation_reason": "The commentary names a neighbour."})
        after = self.app.create_report()
        self.assertEqual(before["character_view"]["denominators"]["aliased_character_entries"], 1)
        self.assertEqual(after["character_view"]["denominators"]["aliased_character_entries"], 0)
        self.assertNotEqual(before["report_id"], after["report_id"])
        self.assertEqual(self.app.catalog.get_story("tale-a", 1)["characters"][0]["name"], "Elder")


class ReplayTests(AppCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.import_story(story("tale-a", tradition="coast"), "coast-batch")
        self.app.import_batch(batch([alternatives()], "mountain-batch"))
        self.add_votes("tale-a")
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        self.report = self.app.create_report()

    def test_replay_reproduces_a_stored_report_exactly(self):
        replay = self.app.replay_report(self.report["report_id"])
        self.assertTrue(replay["reproduces_stored_report"])
        self.assertTrue(replay["decisions_unchanged"])
        self.assertEqual(replay["replayed_report_id"], self.report["report_id"])
        self.assertEqual(replay["missing_decisions"], [])
        self.assertEqual(replay["missing_adjudications"], [])

    def test_replay_with_a_mismatched_policy_identifier_is_refused(self):
        with self.assertRaises(ConflictError):
            self.app.replay_report(self.report["report_id"], policy_id="0" * 64)
        accepted = self.app.replay_report(self.report["report_id"], policy_id=replay_policy(self.report))
        self.assertTrue(accepted["reproduces_stored_report"])

    def test_replay_unknown_report_is_a_domain_error(self):
        with self.assertRaises(NotFoundError):
            self.app.replay_report("absent-report")

    def test_newer_decision_does_not_silently_enter_an_old_report_replay(self):
        self.app.record_decision(decision(annotator="reader-a", sequence=2, pattern="harm"))
        replay = self.app.replay_report(self.report["report_id"])
        self.assertTrue(replay["reproduces_stored_report"])
        self.assertEqual(replay["newer_decisions_not_used"], ["tale-a-reader-a-2"])
        self.assertEqual(replay["replayed_summary"]["pattern_counts"], {"parallel": 1})

    def test_alias_added_later_is_shown_as_a_separate_comparison(self):
        self.app.record_character_alias(alias(story_tradition="mountain", raw_name="Oldest",
                                              canonical_id="elder", alias_id="alias-mountain"))
        replay = self.app.replay_report(self.report["report_id"])
        self.assertTrue(replay["reproduces_stored_report"])
        self.assertTrue(replay["policy_changed"])
        self.assertEqual(replay["policy_differences"]["added_mappings"][0]["raw_name"], "Oldest")
        self.assertEqual(replay["replayed_summary"]["aliased_character_entries"], 1)
        self.assertEqual(replay["current_policy_summary"]["aliased_character_entries"], 2)

    def test_revocation_is_visible_as_a_removed_mapping_on_replay(self):
        self.app.revoke_character_alias({"alias_id": "alias-one", "revoked_by": "editor-two",
                                         "revocation_reason": "Synthetic revision of judgement."})
        replay = self.app.replay_report(self.report["report_id"])
        self.assertEqual(replay["policy_differences"]["removed_mappings"],
                         [{"tradition": "coast", "raw_name": "Elder",
                           "canonical_id": "elder", "alias_id": "alias-one"}])
        self.assertEqual(replay["stored_summary"]["distinct_analysis_characters"], 6)
        self.assertEqual(replay["stored_summary"]["aliased_character_entries"], 1)
        self.assertEqual(replay["current_policy_summary"]["aliased_character_entries"], 0)

    def test_revision_reimport_is_detected_rather_than_mixing_versions(self):
        with self.app.store.transaction() as connection:
            connection.execute("DELETE FROM batch_stories WHERE story_id='tale-b' AND revision=1")
            connection.execute("DELETE FROM stories WHERE story_id='tale-b' AND revision=1")
        with self.assertRaisesRegex(ConflictError, "no longer available"):
            self.app.replay_report(self.report["report_id"])

    def test_replay_of_report_without_frozen_policy_is_refused(self):
        with self.app.store.transaction() as connection:
            document = connection.execute("SELECT document FROM reports WHERE report_id=?",
                                          (self.report["report_id"],)).fetchone()["document"]
            import json
            body = json.loads(document)
            body["provenance"].pop("character_policy")
            body.pop("report_id")
            connection.execute("UPDATE reports SET document=? WHERE report_id=?",
                               (json.dumps(body, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")), self.report["report_id"]))
        with self.assertRaisesRegex(ConflictError, "frozen character policy"):
            self.app.replay_report(self.report["report_id"])


def adjudication(story_id="tale-a", revision=1, record_id="resolution-1", sequence=1):
    return {
        "adjudication_id": record_id, "story_id": story_id, "story_revision": revision,
        "moderator": "editor-1", "sequence": sequence, "pattern": "harm",
        "youngest_outcome": "wins", "evidence_event_ids": ["first", "third"],
        "rationale": "Synthetic moderator resolution for replay coverage.",
    }


class ReplayAdjudicationTests(AppCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.import_story()
        self.add_votes(values=[
            decision(annotator="reader-a", pattern="parallel", outcome="wins"),
            decision(annotator="reader-b", pattern="harm", outcome="loses"),
        ])
        self.app.record_adjudication(adjudication())
        self.report = self.app.create_report()

    def test_replay_uses_the_adjudication_recorded_in_the_report(self):
        replay = self.app.replay_report(self.report["report_id"])
        self.assertTrue(replay["reproduces_stored_report"])
        self.assertTrue(replay["decisions_unchanged"])
        self.assertEqual(replay["replayed_summary"]["pattern_consensus"], 0)
        self.assertEqual(replay["newer_adjudications_not_used"], [])

    def test_later_adjudication_is_listed_but_not_applied_to_old_replay(self):
        self.app.record_adjudication(adjudication(record_id="resolution-2", sequence=2))
        replay = self.app.replay_report(self.report["report_id"])
        self.assertTrue(replay["reproduces_stored_report"])
        self.assertEqual(replay["newer_adjudications_not_used"], ["resolution-2"])
        self.assertEqual(self.app.stored_report(self.report["report_id"])["story_results"][0]
                         ["final_pattern"], "harm")

    def test_replay_reports_a_deleted_adjudication_instead_of_silently_dropping_it(self):
        with self.app.store.transaction() as connection:
            connection.execute("DELETE FROM adjudications WHERE adjudication_id='resolution-1'")
        replay = self.app.replay_report(self.report["report_id"])
        self.assertFalse(replay["reproduces_stored_report"])
        self.assertFalse(replay["decisions_unchanged"])
        self.assertEqual(replay["missing_adjudications"], ["resolution-1"])


class AliasAuditTests(AppCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.import_story()

    def test_clean_alias_registry_and_report_pass_audit(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        self.add_votes()
        self.app.create_report()
        result = self.app.audit()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["aliases_checked"], 1)

    def test_audit_detects_a_tampered_alias_digest(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        with self.app.store.transaction() as connection:
            connection.execute("UPDATE character_aliases SET raw_name='Someone Else'")
        result = self.app.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["scope"] == "character_alias" for item in result["issues"]))

    def test_audit_detects_a_report_whose_frozen_policy_was_edited(self):
        self.app.record_character_alias(alias(raw_name="Elder", canonical_id="elder"))
        report = self.app.create_report()
        import json
        with self.app.store.transaction() as connection:
            document = json.loads(connection.execute(
                "SELECT document FROM reports WHERE report_id=?", (report["report_id"],)
            ).fetchone()["document"])
            document["provenance"]["character_policy"]["mappings"] = []
            document.pop("report_id")
            connection.execute("UPDATE reports SET document=? WHERE report_id=?",
                               (json.dumps(document, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")), report["report_id"]))
        result = self.app.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["scope"] == "report" for item in result["issues"]))


def replay_policy(report):
    return report["provenance"]["character_policy"]["policy_id"]


if __name__ == "__main__":
    unittest.main()
