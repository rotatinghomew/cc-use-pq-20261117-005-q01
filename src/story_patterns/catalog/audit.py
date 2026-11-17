from __future__ import annotations

import json
from collections import defaultdict

from story_patterns.catalog.validate import digest_json, normalize_story
from story_patterns.errors import ValidationError
from story_patterns.review.adjudication import normalize_adjudication
from story_patterns.review.validate import normalize_decision


def audit_catalog(snapshot: dict) -> dict:
    issues = []
    stories = {}
    for row in snapshot["stories"]:
        identity = (row["story_id"], row["revision"])
        try:
            document = json.loads(row["document"])
            normalized = normalize_story(document)
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            issues.append(_issue("story", identity, f"invalid story document: {error}"))
            continue
        if normalized != document or (normalized["title"], normalized["tradition"], normalized["source"]["kind"]) != (
                row["title"], row["tradition"], row["source_kind"]):
            issues.append(_issue("story", identity, "document and indexed columns disagree"))
        stories[identity] = normalized
    batch_rows = {row["batch_id"]: row for row in snapshot["batches"]}
    grouped = defaultdict(list)
    for relation in snapshot["batch_stories"]:
        identity = (relation["story_id"], relation["revision"])
        grouped[relation["batch_id"]].append(identity)
        if identity not in stories:
            issues.append(_issue("batch", relation["batch_id"], f"references missing story revision {identity}"))
    for batch_id, row in batch_rows.items():
        identities = sorted(grouped.get(batch_id, []))
        if row["story_count"] != len(identities):
            issues.append(_issue("batch", batch_id, "declared story count differs from linked records"))
        if not identities or any(identity not in stories for identity in identities):
            continue
        payload = {"batch_id": batch_id, "stories": [stories[identity] for identity in identities]}
        if digest_json(payload) != row["digest"]:
            issues.append(_issue("batch", batch_id, "content digest differs from linked story revisions"))
    origins_by_identity = grouped_by_identity(grouped)
    for identity in stories:
        if not origins_by_identity.get(identity):
            issues.append(_issue("story", identity, "story revision has no import-batch provenance"))
    for row in snapshot["decisions"]:
        identity = (row["story_id"], row["story_revision"])
        story = stories.get(identity)
        if story is None:
            issues.append(_issue("decision", row["decision_id"], "references missing story revision"))
            continue
        try:
            payload = {key: row[key] for key in ("decision_id", "story_id", "story_revision", "annotator", "sequence",
                                                  "pattern", "youngest_outcome", "note")}
            payload["evidence_event_ids"] = json.loads(row["evidence_event_ids"])
            normalized = normalize_decision(payload, story)
            if digest_json(normalized) != row["digest"]:
                issues.append(_issue("decision", row["decision_id"], "stored decision digest does not match content"))
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            issues.append(_issue("decision", row["decision_id"], f"invalid decision: {error}"))
    for row in snapshot["adjudications"]:
        identity = (row["story_id"], row["story_revision"])
        story = stories.get(identity)
        if story is None:
            issues.append(_issue("adjudication", row["adjudication_id"], "references missing story revision"))
            continue
        try:
            payload = {key: row[key] for key in ("adjudication_id", "story_id", "story_revision", "moderator",
                                                  "sequence", "pattern", "youngest_outcome", "rationale")}
            payload["evidence_event_ids"] = json.loads(row["evidence_event_ids"])
            normalized = normalize_adjudication(payload, story)
            if digest_json(normalized) != row["digest"]:
                issues.append(_issue("adjudication", row["adjudication_id"], "stored digest does not match content"))
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            issues.append(_issue("adjudication", row["adjudication_id"], f"invalid adjudication: {error}"))
    story_keys = {f"{story_id}@{revision}" for story_id, revision in stories}
    decision_ids = {row["decision_id"] for row in snapshot["decisions"]}
    adjudication_ids = {row["adjudication_id"] for row in snapshot["adjudications"]}
    aliases_checked, alias_issues = _audit_aliases(snapshot)
    issues.extend(alias_issues)
    for row in snapshot["reports"]:
        try:
            document = json.loads(row["document"])
            body = {key: value for key, value in document.items() if key != "report_id"}
            if document.get("report_id") != row["report_id"] or digest_json(body)[:24] != row["report_id"]:
                issues.append(_issue("report", row["report_id"], "report content does not match its identifier"))
                continue
            provenance = document["provenance"]
            if not set(provenance["story_revisions"]).issubset(story_keys):
                issues.append(_issue("report", row["report_id"], "references missing story revisions"))
            if not set(provenance["decision_ids"]).issubset(decision_ids):
                issues.append(_issue("report", row["report_id"], "references missing reader decisions"))
            if not set(provenance["adjudication_ids"]).issubset(adjudication_ids):
                issues.append(_issue("report", row["report_id"], "references missing adjudications"))
            for identity, batch_ids in provenance.get("batch_ids_by_story", {}).items():
                if identity not in story_keys or not set(batch_ids).issubset(batch_rows):
                    issues.append(_issue("report", row["report_id"], "references missing batch provenance"))
            policy = provenance.get("character_policy")
            if policy is not None:
                from story_patterns.catalog.aliases import policy_snapshot
                recomputed = policy_snapshot(policy.get("canonicals", []), [
                    dict(item) | {"status": "active"} for item in policy.get("mappings", [])
                ])
                if recomputed["policy_id"] != policy.get("policy_id"):
                    issues.append(_issue("report", row["report_id"],
                                         "frozen character policy does not match its identifier"))
                if (document["criteria"].get("character_policy_id")
                        != provenance.get("character_policy", {}).get("policy_id")):
                    issues.append(_issue("report", row["report_id"],
                                         "criteria and provenance disagree on character policy"))
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            issues.append(_issue("report", row["report_id"], f"invalid report document: {error}"))
    issues.sort(key=lambda item: (item["scope"], str(item["identity"]), item["problem"]))
    return {"ok": not issues, "story_revisions_checked": len(snapshot["stories"]),
            "batches_checked": len(snapshot["batches"]), "decisions_checked": len(snapshot["decisions"]),
            "batch_links_checked": len(snapshot["batch_stories"]),
            "adjudications_checked": len(snapshot["adjudications"]), "reports_checked": len(snapshot["reports"]),
            "aliases_checked": aliases_checked,
            "issue_count": len(issues), "issues": issues}


def _audit_aliases(snapshot: dict) -> tuple[int, list[dict]]:
    from story_patterns.catalog.aliases import alias_payload, canonical_payload

    issues = []
    canonicals = {}
    for row in snapshot.get("canonical_characters", []):
        canonical_id = row["canonical_id"]
        payload = canonical_payload(canonical_id, row["canonical_name"], row["created_by"])
        if digest_json(payload) != row["digest"]:
            issues.append(_issue("canonical_character", canonical_id, "stored digest does not match content"))
        canonicals[canonical_id] = row
    active_names = {}
    for row in snapshot.get("character_aliases", []):
        alias_id = row["alias_id"]
        if digest_json(alias_payload(row)) != row["digest"]:
            issues.append(_issue("character_alias", alias_id, "stored digest does not match content"))
        if row["canonical_id"] not in canonicals:
            issues.append(_issue("character_alias", alias_id, "references missing canonical character"))
        if row["status"] == "active":
            key = (row["tradition"], row["raw_name"])
            if key in active_names:
                issues.append(_issue("character_alias", alias_id,
                                     f"duplicates an active mapping for {key[0]}/{key[1]}"))
            active_names[key] = alias_id
        elif row["status"] == "revoked" and not row["revoked_by"]:
            issues.append(_issue("character_alias", alias_id, "revocation has no attributed reviewer"))
    return len(snapshot.get("character_aliases", [])), issues


def grouped_by_identity(grouped: dict) -> dict:
    result = defaultdict(list)
    for batch_id, identities in grouped.items():
        for identity in identities:
            result[identity].append(batch_id)
    return result


def _issue(scope: str, identity, problem: str) -> dict:
    return {"scope": scope, "identity": identity, "problem": problem}
