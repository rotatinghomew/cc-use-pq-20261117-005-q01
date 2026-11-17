import unittest

from story_patterns.analysis.comparison import compare_traditions
from story_patterns.analysis.markdown import render_report
from story_patterns.catalog.history import lineage_warnings, revision_lineage
from story_patterns.catalog.manifest import build_manifest
from story_patterns.catalog.preview import preview_rows
from story_patterns.errors import ConflictError, NotFoundError, ValidationError
from tests.support import AppCase, batch, decision, story


def adjudication(story_id="tale-a", revision=1, record_id="resolution-1", sequence=1,
                 pattern="harm", outcome="wins", evidence=None):
    return {
        "adjudication_id": record_id, "story_id": story_id, "story_revision": revision,
        "moderator": "editor-1", "sequence": sequence, "pattern": pattern,
        "youngest_outcome": outcome,
        "evidence_event_ids": ["first", "third"] if evidence is None else evidence,
        "rationale": "The later episode reverses the earlier harm.",
    }


class PreviewAndCatalogHistoryTests(AppCase, unittest.TestCase):
    def test_preview_collects_separate_row_errors_and_identifies_batch_duplicates(self):
        malformed = story("invalid")
        malformed["events"][0]["actor_id"] = "not-in-roster"
        rows = [story("valid"), malformed, story("repeat"), story("repeat")]
        result = preview_rows(rows)
        self.assertFalse(result["ready"])
        self.assertEqual(result["rows_seen"], 4)
        self.assertEqual(result["valid_rows"], 1)
        self.assertEqual(result["error_count"], 3)
        self.assertEqual([item["row"] for item in result["errors"]], [2, 3, 4])
        self.assertEqual(self.app.catalog.list_stories(), [])

    def test_valid_preview_is_read_only_and_normalizes_the_same_as_import(self):
        row = story()
        result = preview_rows([row])
        self.assertTrue(result["ready"])
        self.assertEqual(result["normalized_stories"][0]["story_id"], "tale-a")
        self.assertEqual(self.app.catalog.list_stories(), [])

    def test_revision_lineage_reports_gaps_without_rewriting_history(self):
        one, three = story(revision=1), story(revision=3)
        self.app.import_batch(batch([one, three], "sparse-history"))
        lineage = self.app.revision_lineage()[0]
        self.assertEqual(lineage["missing_revision_numbers"], [2])
        self.assertEqual(lineage["latest_revision"], 3)
        self.assertEqual(lineage_warnings([lineage])[0]["story_id"], "tale-a")

    def test_complete_revision_lineage_has_no_warning(self):
        self.app.import_batch(batch([story(revision=1), story(revision=2)], "complete-history"))
        rows = revision_lineage(self.app.catalog.list_stories())
        self.assertEqual(rows[0]["missing_revision_numbers"], [])
        self.assertEqual(lineage_warnings(rows), [])

    def test_dataset_manifest_preserves_every_batch_relationship(self):
        self.app.import_batch(batch([story()], "origin-one"))
        self.app.import_batch(batch([story()], "origin-two"))
        manifest = self.app.catalog_manifest()
        self.assertEqual(manifest["batch_count"], 2)
        self.assertEqual(manifest["story_revision_count"], 1)
        self.assertEqual([row["linked_story_count"] for row in manifest["batches"]], [1, 1])
        report = self.app.create_report()
        self.assertEqual(report["provenance"]["batch_ids_by_story"], {"tale-a@1": ["origin-one", "origin-two"]})

    def test_integrity_audit_checks_linked_batch_digests_and_reports(self):
        self.import_story()
        self.add_votes()
        self.app.create_report()
        result = self.app.audit()
        self.assertTrue(result["ok"])
        self.assertEqual(result["story_revisions_checked"], 1)
        self.assertEqual(result["reports_checked"], 1)

    def test_audit_finds_tampered_report_content_after_storage_write(self):
        self.import_story()
        self.app.create_report()
        with self.app.store.transaction() as connection:
            connection.execute("UPDATE reports SET document='{}'")
        result = self.app.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any(item["scope"] == "report" for item in result["issues"]))

    def test_audit_finds_tampered_batch_digest_without_losing_story_records(self):
        self.import_story()
        with self.app.store.transaction() as connection:
            connection.execute("UPDATE import_batches SET digest='wrong'")
        result = self.app.audit()
        self.assertFalse(result["ok"])
        self.assertTrue(any("content digest" in item["problem"] for item in result["issues"]))


class SearchAndComparisonTests(AppCase, unittest.TestCase):
    def test_search_matches_narrative_evidence_and_returns_matching_events(self):
        self.import_story()
        result = self.app.search_stories(text="shelter")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["matching_event_ids"], ["third"])

    def test_search_can_combine_source_tradition_and_sibling_role_filters(self):
        coast = story("coast", tradition="shore")
        upland = story("upland", tradition="upland")
        upland["source"]["kind"] = "book"
        self.app.import_batch(batch([coast, upland], "source-groups"))
        result = self.app.search_stories(tradition="upland", source_kind="book", sibling_order=2)
        self.assertEqual([item["story_id"] for item in result], ["upland"])

    def test_latest_policy_selects_latest_revision_before_text_matching(self):
        old, current = story(revision=1), story(revision=2)
        old["title"] = "Old title has special clue"
        current["title"] = "Current title"
        self.app.import_batch(batch([old, current], "versions"))
        all_versions = self.app.search_stories(text="special clue", revision_policy="all")
        latest_only = self.app.search_stories(text="special clue", revision_policy="latest")
        self.assertEqual(len(all_versions), 1)
        self.assertEqual(latest_only, [])

    def test_search_rejects_ambiguous_empty_filters_and_invalid_revision_policy(self):
        for criteria in [{"text": " "}, {"tradition": ""}, {"sibling_order": True},
                         {"revision_policy": "newest"}]:
            with self.subTest(criteria=criteria):
                with self.assertRaises(ValidationError):
                    self.app.search_stories(**criteria)

    def test_comparison_keeps_coverage_and_rates_within_each_cohort(self):
        coast, mountain = story("coast", tradition="coast"), story("mountain", tradition="mountain")
        self.app.import_batch(batch([coast, mountain], "two-groups"))
        self.add_votes("coast")
        result = self.app.compare_traditions()
        self.assertEqual([row["tradition"] for row in result["groups"]], ["coast", "mountain"])
        self.assertEqual(result["groups"][0]["pattern_rates"], {"parallel": 1.0})
        self.assertEqual(result["groups"][1]["pattern_coverage"]["insufficient"], 1)
        self.assertIn("denominators", result["interpretation_limit"])

    def test_empty_consensus_group_has_no_fabricated_zero_rate(self):
        self.import_story()
        group = self.app.compare_traditions()["groups"][0]
        self.assertEqual(group["pattern_rates"], {})
        self.assertEqual(group["pattern_consensus"], 0)


class AdjudicationLifecycleTests(AppCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.import_story()
        self.add_votes(values=[
            decision(annotator="reader-a", pattern="parallel", outcome="wins"),
            decision(annotator="reader-b", pattern="harm", outcome="loses"),
        ])

    def test_adjudication_is_separate_from_reader_consensus(self):
        self.app.record_adjudication(adjudication())
        report = self.app.create_report()
        row = report["story_results"][0]
        self.assertIsNone(row["pattern"])
        self.assertEqual(row["pattern_status"], "split")
        self.assertEqual(row["final_pattern"], "harm")
        self.assertEqual(row["resolution"], "adjudicated")
        self.assertEqual(report["pattern_counts"], {})
        self.assertEqual(report["final_pattern_counts"], {"harm": 1})
        self.assertEqual(report["provenance"]["adjudication_ids"], ["resolution-1"])

    def test_adjudication_retry_is_idempotent_but_identifier_reuse_conflicts(self):
        record = adjudication()
        self.assertEqual(self.app.record_adjudication(record)["status"], "recorded")
        self.assertEqual(self.app.record_adjudication(record)["status"], "already_recorded")
        changed = adjudication(pattern="parallel")
        with self.assertRaises(ConflictError):
            self.app.record_adjudication(changed)

    def test_adjudication_requires_valid_label_evidence_and_rationale(self):
        cases = []
        invalid_label = adjudication(pattern="unclear")
        cases.append(invalid_label)
        missing_evidence = adjudication(evidence=[])
        cases.append(missing_evidence)
        false_evidence = adjudication(evidence=["not-an-event"])
        cases.append(false_evidence)
        blank_reason = adjudication()
        blank_reason["rationale"] = " "
        cases.append(blank_reason)
        for record in cases:
            with self.subTest(record=record):
                with self.assertRaises(ValidationError):
                    self.app.record_adjudication(record)

    def test_later_adjudication_supersedes_earlier_without_deleting_it(self):
        self.app.record_adjudication(adjudication())
        later = adjudication(record_id="resolution-2", sequence=2, pattern="parallel")
        self.app.record_adjudication(later)
        report = self.app.create_report()
        self.assertEqual(report["story_results"][0]["final_pattern"], "parallel")
        self.assertEqual(report["provenance"]["adjudication_ids"], ["resolution-2"])
        history = self.app.review_history("tale-a", 1)
        self.assertEqual([item["adjudication_id"] for item in history["adjudications"]],
                         ["resolution-1", "resolution-2"])

    def test_adjudication_sequence_collision_does_not_change_report(self):
        self.app.record_adjudication(adjudication())
        before = self.app.create_report()
        collision = adjudication(record_id="other-resolution")
        with self.assertRaises(ConflictError):
            self.app.record_adjudication(collision)
        after = self.app.create_report()
        self.assertEqual(before["report_id"], after["report_id"])

    def test_queue_marks_adjudicated_records_separately(self):
        self.app.record_adjudication(adjudication())
        queue = self.app.review_queue()
        self.assertEqual(queue["by_status"]["adjudicated"], 1)
        self.assertEqual(queue["stories"][0]["final_pattern"], "harm")

    def test_missing_story_adjudication_and_history_return_domain_errors(self):
        with self.assertRaises(NotFoundError):
            self.app.record_adjudication(adjudication(story_id="absent"))
        with self.assertRaises(NotFoundError):
            self.app.review_history("absent", 1)

    def test_markdown_report_explains_consensus_and_final_resolution(self):
        self.app.record_adjudication(adjudication())
        report = self.app.create_report()
        markdown = render_report(report)
        self.assertIn("Independent-reader consensus counts", markdown)
        self.assertIn("Final pattern counts", markdown)
        self.assertIn(report["report_id"], markdown)


class AliasWorkflowCliTests(unittest.TestCase):
    def test_alias_cli_commands_keep_history_and_replay_a_report(self):
        import contextlib
        import io
        import json
        import tempfile
        from pathlib import Path
        from story_patterns.cli import main

        fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / "multitranslation.json"
        content = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertTrue(all(story_data["source"]["kind"] == "local_synthetic"
                            for story_data in content["batch"]["stories"]))
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "aliases.sqlite3")

            def run(*arguments):
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = main([*arguments, "--database", database])
                self.assertEqual(code, 0, stderr.getvalue())
                return json.loads(stdout.getvalue())

            story_file = Path(directory) / "stories.json"
            story_file.write_text(json.dumps(content["batch"]["stories"], ensure_ascii=False),
                                  encoding="utf-8")
            run("import", "--file", str(story_file), "--format", "json",
                "--batch-id", content["batch"]["batch_id"])
            listing = run("alias-list")
            self.assertEqual(listing["active_mapping_count"], 0)

            alias_file = Path(directory) / "alias.json"
            alias_file.write_text(json.dumps({
                "alias_id": "coast-elder", "tradition": "coastal_copy", "raw_name": "阿甲",
                "canonical_id": "elder", "canonical_name": "长兄",
                "rationale": "合成对照：同一事件位置。", "created_by": "editor-test",
            }, ensure_ascii=False), encoding="utf-8")
            run("alias-add", "--file", str(alias_file))
            report = run("report")
            self.assertEqual(report["character_view"]["denominators"]["aliased_character_entries"], 1)

            revoke_file = Path(directory) / "revoke.json"
            revoke_file.write_text(json.dumps({
                "alias_id": "coast-elder", "revoked_by": "editor-test",
                "revocation_reason": "合成复核撤回。",
            }, ensure_ascii=False), encoding="utf-8")
            run("alias-revoke", "--file", str(revoke_file))
            history = run("alias-list")
            self.assertEqual(history["active_mapping_count"], 0)
            self.assertEqual(history["revoked_mapping_count"], 1)
            self.assertEqual(history["aliases"][0]["raw_name"], "阿甲")

            replay = run("replay", "--report-id", report["report_id"])
            self.assertTrue(replay["reproduces_stored_report"])
            self.assertTrue(replay["policy_changed"])
            self.assertEqual(replay["policy_differences"]["removed_mappings"][0]["raw_name"], "阿甲")
            self.assertEqual(run("audit")["issue_count"], 0)


    def test_demo_fixture_exercises_merges_collisions_revisions_and_revocation(self):
        import contextlib
        import io
        import json
        import tempfile
        from pathlib import Path
        from story_patterns.cli import main

        fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / "multitranslation.json"
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "demo.sqlite3")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["demo", "--database", database, "--fixture", str(fixture_path)])
        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        view = output["report"]["character_view"]
        self.assertEqual(view["denominators"]["aliased_character_entries"], 6)
        self.assertEqual(view["denominators"]["collision_story_revisions"], 1)
        self.assertEqual(view["collisions"][0]["kind"], "ambiguous_raw_name")
        youngest = next(row for row in view["characters"]
                        if row["analysis_id"] == "canonical:youngest-sibling")
        self.assertEqual(sorted(youngest["raw_names"]), ["小弟", "阿丙"])
        revision_names = {
            (story_row["revision"], character["raw_name"])
            for story_row in view["stories"]
            if story_row["story_id"] == "two-names-one-house"
            for character in story_row["characters"]
        }
        self.assertIn((1, "阿甲"), revision_names)
        self.assertIn((2, "阿甲（守门人）"), revision_names)
        after = output["report_after_revocation"]["character_view"]
        self.assertEqual(after["denominators"]["aliased_character_entries"], 5)
        replay = output["replay"]
        self.assertTrue(replay["reproduces_stored_report"])
        self.assertTrue(replay["decisions_unchanged"])
        self.assertEqual(replay["policy_differences"]["removed_mappings"],
                         [{"tradition": "harbor_copy", "raw_name": "小弟",
                           "canonical_id": "youngest-sibling", "alias_id": "alias-harbor-c"}])


if __name__ == "__main__":
    unittest.main()
