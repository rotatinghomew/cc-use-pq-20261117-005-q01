from __future__ import annotations

from collections import Counter
from typing import Any

from story_patterns.catalog.validate import canonical_json, digest_json
from story_patterns.analysis.agreement import pairwise_agreement
from story_patterns.analysis.evidence import evidence_coverage
from story_patterns.analysis.sensitivity import leave_one_reader_out
from story_patterns.analysis.revision_drift import compare_revisions
from story_patterns.analysis.votes import vote_distribution
from story_patterns.analysis.sample_profile import build_sample_profile
from story_patterns.analysis.crosstab import consensus_crosstab
from story_patterns.analysis.characters import (build_character_view, compare_canonical_characters,
                                               scoped_policy, observed_pairs)
from story_patterns.catalog.history import lineage_warnings, revision_lineage
from story_patterns.errors import ConflictError, NotFoundError


def _consensus(votes: list[str], minimum: int) -> tuple[str | None, str]:
    usable = [vote for vote in votes if vote not in {"unclear", "unknown"}]
    if len(usable) < minimum:
        return None, "insufficient"
    counts = Counter(usable)
    ordered = counts.most_common()
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return None, "split"
    return ordered[0][0], "agreed"


def build_report(stories: list[dict], decisions: dict[tuple[str, int], list[dict]],
                 tradition: str | None, minimum: int, origins: dict | None = None,
                 adjudications: dict | None = None, policy: dict | None = None) -> dict[str, Any]:
    pattern_counts: Counter = Counter()
    outcome_counts: Counter = Counter()
    final_pattern_counts: Counter = Counter()
    final_outcome_counts: Counter = Counter()
    pattern_coverage: Counter = Counter()
    outcome_coverage: Counter = Counter()
    resolution_counts: Counter = Counter()
    used_decisions = []
    story_results = []
    for story in stories:
        identity = (story["story_id"], story["revision"])
        votes = decisions.get(identity, [])
        pattern, pattern_status = _consensus([item["pattern"] for item in votes], minimum)
        outcome, outcome_status = _consensus([item["youngest_outcome"] for item in votes], minimum)
        adjudication = (adjudications or {}).get(identity)
        resolution_counts["adjudicated" if adjudication else "reader_consensus"] += 1
        pattern_coverage[pattern_status] += 1
        outcome_coverage[outcome_status] += 1
        if pattern is not None:
            pattern_counts[pattern] += 1
        if outcome is not None:
            outcome_counts[outcome] += 1
        final_pattern = adjudication["pattern"] if adjudication else pattern
        final_outcome = adjudication["youngest_outcome"] if adjudication else outcome
        if final_pattern is not None:
            final_pattern_counts[final_pattern] += 1
        if final_outcome not in {None, "unknown"}:
            final_outcome_counts[final_outcome] += 1
        used_decisions.extend(item["decision_id"] for item in votes)
        story_results.append({
            "story_id": story["story_id"], "revision": story["revision"],
            "title": story["title"], "tradition": story["tradition"],
            "pattern": pattern, "pattern_status": pattern_status,
            "youngest_outcome": outcome, "outcome_status": outcome_status,
            "final_pattern": final_pattern, "final_outcome": final_outcome,
            "resolution": "adjudicated" if adjudication else "reader_consensus",
            "adjudication_id": adjudication["adjudication_id"] if adjudication else None,
            "adjudication_rationale": adjudication["rationale"] if adjudication else None,
            "adjudication_evidence_event_ids": adjudication["evidence_event_ids"] if adjudication else [],
            "reviewer_count": len(votes),
        })
    character_view = build_character_view(stories, decisions, policy)
    return {
        "criteria": {"tradition": tradition, "minimum_reviewers": minimum,
                     "unclear_and_unknown_are_abstentions": True,
                     "character_policy_id": character_view["policy_id"]},
        "population": {"story_revisions": len(stories), "pattern_consensus": sum(pattern_counts.values()),
                       "outcome_consensus": sum(outcome_counts.values())},
        "coverage": {
            "pattern": {key: pattern_coverage.get(key, 0) for key in ("agreed", "split", "insufficient")},
            "outcome": {key: outcome_coverage.get(key, 0) for key in ("agreed", "split", "insufficient")},
        },
        "pattern_counts": dict(sorted(pattern_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "final_pattern_counts": dict(sorted(final_pattern_counts.items())),
        "final_outcome_counts": dict(sorted(final_outcome_counts.items())),
        "adjudicated_story_revisions": sum(value is not None for value in (adjudications or {}).values()),
        "resolution_counts": {key: resolution_counts.get(key, 0)
                              for key in ("reader_consensus", "adjudicated")},
        "story_results": story_results,
        "reviewer_agreement": pairwise_agreement(decisions),
        "evidence_coverage": evidence_coverage(stories, decisions),
        "reader_sensitivity": leave_one_reader_out(stories, decisions, minimum),
        "reader_vote_distribution": vote_distribution(stories, decisions),
        "sample_profile": build_sample_profile(stories),
        "pattern_outcome_crosstab": consensus_crosstab(story_results),
        "character_view": character_view,
        "canonical_character_comparison": compare_canonical_characters(character_view),
        "revision_lineage": {"scope": "selected story revisions", "stories": revision_lineage(stories),
                             "warnings": lineage_warnings(revision_lineage(stories))},
        "revision_changes": compare_revisions(stories),
        "provenance": {
            "story_revisions": [f"{item['story_id']}@{item['revision']}" for item in stories],
            "decision_ids": sorted(used_decisions),
            "adjudication_ids": sorted(value["adjudication_id"] for value in (adjudications or {}).values() if value),
            "batch_ids_by_story": {
                f"{story_id}@{revision}": sorted(batch_ids)
                for (story_id, revision), batch_ids in sorted((origins or {}).items())
            },
            "character_policy_id": character_view["policy_id"],
            "character_policy": character_view["policy"],
        },
    }


class ReportService:
    def __init__(self, store):
        self.store = store

    def create(self, tradition: str | None, minimum: int) -> dict:
        from story_patterns.analysis.cohorts import select_cohort, validate_minimum

        minimum = validate_minimum(minimum)
        (stories, decisions, origins, adjudications,
         canonicals, aliases) = self.store.analysis_snapshot_with_aliases(tradition)
        stories = select_cohort(stories, tradition)
        traditions = {item["tradition"] for item in stories}
        policy = scoped_policy(canonicals, aliases, traditions, used_pairs=observed_pairs(stories))
        result = build_report(stories, decisions, tradition, minimum, origins, adjudications,
                              policy=policy)
        report_id = digest_json(result)[:24]
        request_digest = digest_json({"criteria": result["criteria"], "provenance": result["provenance"]})
        document = canonical_json({"report_id": report_id, **result})
        with self.store.transaction() as connection:
            previous = connection.execute("SELECT document FROM reports WHERE report_id=?", (report_id,)).fetchone()
            if previous and previous["document"] != document:
                raise ConflictError("report identifier collision with different contents")
            connection.execute(
                "INSERT OR IGNORE INTO reports(report_id, request_digest, document) VALUES(?,?,?)",
                (report_id, request_digest, document),
            )
        return {"report_id": report_id, **result}

    def get(self, report_id: str) -> dict | None:
        return self.store.report_document(report_id)

    def replay(self, report_id: str, policy_id: str | None = None) -> dict:
        """按报告冻结的修订与人物口径复算；另给出当前口径下的对照，不改动任何判断。"""
        from story_patterns.analysis.cohorts import select_cohort, validate_minimum

        stored = self.store.report_document(report_id)
        if stored is None:
            raise NotFoundError(f"report {report_id!r} was not found")
        criteria = stored["criteria"]
        minimum = validate_minimum(criteria["minimum_reviewers"])
        tradition = criteria["tradition"]
        frozen = stored["provenance"].get("character_policy")
        if frozen is None:
            raise ConflictError("stored report has no frozen character policy and cannot be replayed")
        if policy_id is not None and policy_id != frozen["policy_id"]:
            raise ConflictError(
                f"policy {policy_id!r} does not match the report's frozen policy "
                f"{frozen['policy_id']!r}"
            )
        recorded = set(stored["provenance"]["story_revisions"])
        (stories, decisions, origins, adjudications,
         canonicals, aliases) = self.store.analysis_snapshot_with_aliases(tradition)
        stories = select_cohort(stories, tradition)
        stories = [story for story in stories if f"{story['story_id']}@{story['revision']}" in recorded]
        missing = sorted(recorded - {f"{story['story_id']}@{story['revision']}" for story in stories})
        if missing:
            raise ConflictError(
                "stored report references story revisions that are no longer available: "
                + ", ".join(missing)
            )
        identities = {(story["story_id"], story["revision"]) for story in stories}
        recorded_decisions = set(stored["provenance"]["decision_ids"])
        recorded_adjudications = set(stored["provenance"]["adjudication_ids"])
        decision_rows = self.store.decision_records(sorted(recorded_decisions))
        adjudication_rows = self.store.adjudication_records(sorted(recorded_adjudications))
        replay_decisions = {}
        for identity in identities:
            replay_decisions[identity] = sorted(
                (row for row in decision_rows.values()
                 if (row["story_id"], row["story_revision"]) == identity),
                key=lambda item: item["annotator"],
            )
        replay_adjudications = {
            identity: next((row for row in adjudication_rows.values()
                            if (row["story_id"], row["story_revision"]) == identity), None)
            for identity in identities
        }
        unavailable_decisions = sorted(recorded_decisions - set(decision_rows))
        unavailable_adjudications = sorted(recorded_adjudications - set(adjudication_rows))
        latest_ids = self.store.latest_decision_ids(sorted(identities))
        latest_adjudication_ids = self.store.latest_adjudication_ids(sorted(identities))
        origins = {identity: origins.get(identity, []) for identity in identities}
        current = scoped_policy(canonicals, aliases, {story["tradition"] for story in stories},
                                used_pairs=observed_pairs(stories))
        replayed = build_report(stories, replay_decisions, tradition, minimum, origins,
                                replay_adjudications, policy=frozen)
        current_report = build_report(stories, replay_decisions, tradition, minimum, origins,
                                      replay_adjudications, policy=current)
        stored_body = {key: value for key, value in stored.items() if key != "report_id"}
        return {
            "report_id": report_id,
            "replayed_report_id": digest_json(replayed)[:24],
            "reproduces_stored_report": digest_json(replayed) == digest_json(stored_body),
            "story_revisions_replayed": sorted(recorded),
            "frozen_policy_id": frozen["policy_id"],
            "current_policy_id": current["policy_id"],
            "policy_changed": frozen["policy_id"] != current["policy_id"],
            "missing_decisions": unavailable_decisions,
            "missing_adjudications": unavailable_adjudications,
            "newer_decisions_not_used": sorted(latest_ids - recorded_decisions),
            "newer_adjudications_not_used": sorted(latest_adjudication_ids - recorded_adjudications),
            "stored_summary": _summary(stored),
            "replayed_summary": _summary(replayed),
            "current_policy_summary": _summary(current_report),
            "character_differences": _character_differences(stored, current_report),
            "policy_differences": _policy_membership_differences(frozen, current),
            "decisions_unchanged": (
                sorted(item["decision_id"] for votes in replay_decisions.values() for item in votes)
                == sorted(recorded_decisions)
                and sorted(item["adjudication_id"] for item in replay_adjudications.values() if item)
                == sorted(recorded_adjudications)
            ),
            "note": ("replay recomputes the report from the exact story revisions, reader decisions "
                     "and adjudications recorded in its provenance, under the character policy frozen "
                     "into that report. Later decisions are listed separately and never rebound; the "
                     "current-policy summary is an additional comparison."),
        }

    def compare(self, minimum: int) -> dict:
        from story_patterns.analysis.cohorts import validate_minimum
        from story_patterns.analysis.comparison import compare_traditions

        minimum = validate_minimum(minimum)
        (stories, decisions, _origins, adjudications,
         canonicals, aliases) = self.store.analysis_snapshot_with_aliases(None)
        policy = scoped_policy(canonicals, aliases, {item["tradition"] for item in stories},
                               used_pairs=observed_pairs(stories))
        return compare_traditions(stories, decisions, minimum, adjudications, policy=policy)


def _summary(report: dict) -> dict:
    return {
        "story_revisions": report["population"]["story_revisions"],
        "pattern_consensus": report["population"]["pattern_consensus"],
        "outcome_consensus": report["population"]["outcome_consensus"],
        "pattern_counts": report["pattern_counts"],
        "coverage": report["coverage"],
        "raw_character_entries": report["character_view"]["denominators"]["raw_character_entries"],
        "aliased_character_entries": report["character_view"]["denominators"]["aliased_character_entries"],
        "unmapped_character_entries": report["character_view"]["denominators"]["unmapped_character_entries"],
        "distinct_analysis_characters": report["character_view"]["denominators"]["distinct_analysis_characters"],
        "collision_story_revisions": report["character_view"]["denominators"]["collision_story_revisions"],
        "character_coverage": report["character_view"]["coverage"],
        "character_ids": sorted(row["analysis_id"] for row in report["character_view"]["characters"]),
        "policy_id": report["character_view"]["policy_id"],
    }


def _character_differences(stored: dict, current: dict) -> list[dict]:
    before = {row["analysis_id"]: row for row in stored["character_view"]["characters"]}
    after = {row["analysis_id"]: row for row in current["character_view"]["characters"]}
    rows = []
    for analysis_id in sorted(before.keys() | after.keys()):
        previous, latest = before.get(analysis_id), after.get(analysis_id)
        if previous == latest:
            continue
        rows.append({
            "analysis_id": analysis_id,
            "display_name": (latest or previous)["display_name"],
            "stored": None if previous is None else _character_facts(previous),
            "current_policy": None if latest is None else _character_facts(latest),
        })
    return rows


def _character_facts(row: dict) -> dict:
    return {
        "basis": row["basis"],
        "canonical_id": row["canonical_id"],
        "raw_names": row["raw_names"],
        "story_revisions": row["story_revisions"],
        "events": row["events"],
        "cited_events": row["cited_events"],
    }


def _policy_membership_differences(frozen: dict, current: dict) -> dict:
    def index(policy):
        return {(item["tradition"], item["raw_name"]): item for item in policy.get("mappings", [])}
    before, after = index(frozen), index(current)
    return {
        "added_mappings": [
            {"tradition": tradition, "raw_name": raw_name,
             "canonical_id": after[(tradition, raw_name)]["canonical_id"],
             "alias_id": after[(tradition, raw_name)]["alias_id"]}
            for tradition, raw_name in sorted(after.keys() - before.keys())
        ],
        "removed_mappings": [
            {"tradition": tradition, "raw_name": raw_name,
             "canonical_id": before[(tradition, raw_name)]["canonical_id"],
             "alias_id": before[(tradition, raw_name)]["alias_id"]}
            for tradition, raw_name in sorted(before.keys() - after.keys())
        ],
        "retargeted_mappings": [
            {"tradition": tradition, "raw_name": raw_name,
             "stored_canonical_id": before[(tradition, raw_name)]["canonical_id"],
             "current_canonical_id": after[(tradition, raw_name)]["canonical_id"]}
            for tradition, raw_name in sorted(before.keys() & after.keys())
            if before[(tradition, raw_name)]["canonical_id"]
            != after[(tradition, raw_name)]["canonical_id"]
        ],
        "replaced_alias_ids": [
            {"tradition": tradition, "raw_name": raw_name,
             "stored_alias_id": before[(tradition, raw_name)]["alias_id"],
             "current_alias_id": after[(tradition, raw_name)]["alias_id"]}
            for tradition, raw_name in sorted(before.keys() & after.keys())
            if before[(tradition, raw_name)]["alias_id"] != after[(tradition, raw_name)]["alias_id"]
        ],
    }
