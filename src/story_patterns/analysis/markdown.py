from __future__ import annotations

import json


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    headings = [label for _, label in columns]
    lines = ["| " + " | ".join(headings) + " |", "| " + " | ".join("---" for _ in headings) + " |"]
    for row in rows:
        cells = [str(row.get(key, "" )).replace("|", "\\|").replace("\n", " ") for key, _ in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_report(report: dict) -> str:
    population = report["population"]
    criteria = report["criteria"]
    lines = [
        f"# Narrative analysis report {report['report_id']}", "",
        "This report summarizes annotated local records. It does not make literary claims on its own.", "",
        "## Scope", "",
        f"- Tradition filter: {criteria['tradition'] or 'all'}",
        f"- Minimum independent readers: {criteria['minimum_reviewers']}",
        f"- Story revisions: {population['story_revisions']}",
        f"- Pattern consensus: {population['pattern_consensus']}",
        f"- Outcome consensus: {population['outcome_consensus']}", "",
        "## Independent-reader consensus counts", "",
    ]
    lines.extend(_table([{"pattern": key, "count": value} for key, value in report["pattern_counts"].items()],
                        [("pattern", "Pattern"), ("count", "Stories")]))
    lines.extend(["", "## Final pattern counts", ""])
    lines.extend(_table([{"pattern": key, "count": value}
                         for key, value in report["final_pattern_counts"].items()],
                        [("pattern", "Pattern"), ("count", "Stories")]))
    lines.extend(["", "## Coverage", "", "Status counts retain unresolved records in the denominator.", ""])
    coverage = report["coverage"]["pattern"]
    lines.extend(_table([{"status": key, "count": value} for key, value in coverage.items()],
                        [("status", "Pattern review status"), ("count", "Stories")]))
    view = report.get("character_view")
    if view is not None:
        denominators = view["denominators"]
        lines.extend([
            "", "## Character names under the analysis policy", "",
            "Raw story names and reader decisions are unchanged. The counts below apply the "
            "frozen alias policy recorded in provenance.", "",
            f"- Policy: `{view['policy_id']}`",
            f"- Raw character entries: {denominators['raw_character_entries']}",
            f"- Resolved through aliases: {denominators['aliased_character_entries']}",
            f"- Left unmapped: {denominators['unmapped_character_entries']}",
            f"- Distinct analysis characters: {denominators['distinct_analysis_characters']}",
            f"- Story revisions with name collisions: {denominators['collision_story_revisions']}", "",
        ])
        rows = [{
            "character": row["display_name"],
            "basis": "alias" if row["basis"] == "alias" else "raw (unmapped)",
            "raw_names": ", ".join(row["raw_names"]),
            "traditions": ", ".join(row["traditions"]),
            "revisions": row["story_revisions"],
            "events": row["events"],
            "cited": row["cited_events"],
        } for row in view["characters"]]
        lines.extend(_table(rows, [("character", "Analysis character"), ("basis", "Basis"),
                                   ("raw_names", "Raw names"),
                                   ("traditions", "Traditions"), ("revisions", "Story revisions"),
                                   ("events", "Events"), ("cited", "Cited events")]))
        if view["collisions"]:
            lines.extend(["", "### Unresolved name collisions", "",
                          "These revisions are excluded from canonical character aggregates until "
                          "reviewers resolve them.", ""])
            lines.extend(_table(view["collisions"],
                                [("story_id", "Story"), ("revision", "Revision"),
                                 ("kind", "Kind"), ("display_name", "Character"),
                                 ("character_ids", "Raw character IDs")]))
    lines.extend(["", "## Provenance", "", "Story revisions and decision IDs used:", "",
                  "```json", json.dumps(report["provenance"], ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)
