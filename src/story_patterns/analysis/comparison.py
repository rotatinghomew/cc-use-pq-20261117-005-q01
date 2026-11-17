from __future__ import annotations

from collections import defaultdict

from story_patterns.analysis.characters import compare_canonical_characters, restrict_policy
from story_patterns.analysis.report import build_report


def compare_traditions(stories: list[dict], decisions: dict, minimum: int,
                       adjudications: dict | None = None, policy: dict | None = None) -> dict:
    grouped = defaultdict(list)
    for story in stories:
        grouped[story["tradition"]].append(story)
    rows = []
    for tradition in sorted(grouped):
        cohort = grouped[tradition]
        identities = {(item["story_id"], item["revision"]) for item in cohort}
        cohort_decisions = {identity: decisions.get(identity, []) for identity in identities}
        cohort_adjudications = {identity: adjudications.get(identity) for identity in identities} if adjudications else None
        cohort_policy = (None if policy is None else restrict_policy(policy, cohort))
        summary = build_report(cohort, cohort_decisions, tradition, minimum,
                               adjudications=cohort_adjudications, policy=cohort_policy)
        pattern_denominator = summary["population"]["pattern_consensus"]
        outcome_denominator = summary["population"]["outcome_consensus"]
        final_pattern_denominator = sum(summary["final_pattern_counts"].values())
        final_outcome_denominator = sum(summary["final_outcome_counts"].values())
        rows.append({
            "tradition": tradition,
            "story_revisions": summary["population"]["story_revisions"],
            "pattern_consensus": pattern_denominator,
            "outcome_consensus": outcome_denominator,
            "pattern_counts": summary["pattern_counts"],
            "pattern_rates": {key: count / pattern_denominator for key, count in summary["pattern_counts"].items()}
            if pattern_denominator else {},
            "outcome_counts": summary["outcome_counts"],
            "outcome_rates": {key: count / outcome_denominator for key, count in summary["outcome_counts"].items()}
            if outcome_denominator else {},
            "pattern_coverage": summary["coverage"]["pattern"],
            "outcome_coverage": summary["coverage"]["outcome"],
            "final_pattern_counts": summary["final_pattern_counts"],
            "final_outcome_counts": summary["final_outcome_counts"],
            "final_pattern_rates": {
                key: count / final_pattern_denominator
                for key, count in summary["final_pattern_counts"].items()
            } if final_pattern_denominator else {},
            "final_outcome_rates": {
                key: count / final_outcome_denominator
                for key, count in summary["final_outcome_counts"].items()
            } if final_outcome_denominator else {},
            "character_denominators": summary["character_view"]["denominators"],
            "character_policy_id": summary["character_view"]["policy_id"],
            "unmapped_character_names": sorted(
                [[tradition, raw_name] for tradition, raw_name in {
                    (item["tradition"], item["raw_name"])
                    for item in summary["character_view"]["unmapped"]
                }]
            ),
            "character_collisions": summary["character_view"]["collisions"],
        })
    all_view_characters = compare_canonical_characters(
        build_report(stories, decisions, None, minimum, adjudications=adjudications,
                     policy=policy)["character_view"]
    )
    return {
        "method": "descriptive comparison of independently filtered cohorts",
        "minimum_reviewers": minimum,
        "groups": rows,
        "canonical_characters": all_view_characters,
        "character_policy_id": None if policy is None else policy["policy_id"],
        "interpretation_limit": (
            "Rates use consensus labels only; coverage and denominators must be reviewed before "
            "comparison. Character rows describe only story revisions whose raw names resolve "
            "through the frozen alias policy."
        ),
    }
