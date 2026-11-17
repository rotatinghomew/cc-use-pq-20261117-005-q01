from __future__ import annotations

from collections import defaultdict

from story_patterns.catalog.aliases import empty_policy


def policy_mapping_index(policy: dict) -> dict[tuple[str, str], dict]:
    return {(item["tradition"], item["raw_name"]): item for item in policy.get("mappings", [])}


def canonical_name_index(policy: dict) -> dict[str, str]:
    return {item["canonical_id"]: item["canonical_name"] for item in policy.get("canonicals", [])}


def scoped_policy(canonicals: list[dict], aliases: list[dict], traditions,
                  used_pairs=None) -> dict:
    """只冻结本报告实际使用到的活跃映射，使 policy_id 与有效归并范围一致。"""
    from story_patterns.catalog.aliases import policy_snapshot

    allowed = set(traditions) if traditions is not None else None
    active = [
        item for item in aliases
        if item["status"] == "active"
        and (allowed is None or item["tradition"] in allowed)
        and (used_pairs is None or (item["tradition"], item["raw_name"]) in used_pairs)
    ]
    referenced = {item["canonical_id"] for item in active}
    return policy_snapshot([item for item in canonicals if item["canonical_id"] in referenced], active)


def observed_pairs(stories: list[dict]) -> set[tuple[str, str]]:
    return {(story["tradition"], character["name"])
            for story in stories for character in story["characters"]}


def restrict_policy(policy: dict, stories: list[dict]) -> dict:
    """从全库口径中取出一个传统分组实际生效的映射。"""
    from story_patterns.catalog.aliases import policy_snapshot

    traditions = {story["tradition"] for story in stories}
    pairs = observed_pairs(stories)
    mappings = [item for item in policy.get("mappings", [])
                if item["tradition"] in traditions
                and (item["tradition"], item["raw_name"]) in pairs]
    referenced = {item["canonical_id"] for item in mappings}
    canonicals = [
        {"canonical_id": item["canonical_id"], "canonical_name": item["canonical_name"]}
        for item in policy.get("canonicals", []) if item["canonical_id"] in referenced
    ]
    return policy_snapshot(canonicals, [
        {"alias_id": item["alias_id"], "tradition": item["tradition"], "raw_name": item["raw_name"],
         "canonical_id": item["canonical_id"], "status": "active"}
        for item in mappings
    ])


def _identity(story: dict, character: dict, mappings: dict, canonicals: dict) -> dict:
    mapping = mappings.get((story["tradition"], character["name"]))
    if mapping is not None and mapping["canonical_id"] in canonicals:
        return {
            "analysis_id": f"canonical:{mapping['canonical_id']}",
            "display_name": canonicals[mapping["canonical_id"]],
            "basis": "alias",
            "canonical_id": mapping["canonical_id"],
            "alias_id": mapping["alias_id"],
        }
    return {
        "analysis_id": f"raw:{story['tradition']}:{character['name']}",
        "display_name": character["name"],
        "basis": "raw",
        "canonical_id": None,
        "alias_id": None,
    }


def build_character_view(stories: list[dict], decisions: dict, policy: dict | None = None) -> dict:
    """在不改动原始记录与判断的前提下，给出规范人物口径的只读分析视图。"""
    policy = policy or empty_policy()
    mappings = policy_mapping_index(policy)
    canonicals = canonical_name_index(policy)
    story_rows = []
    collisions = []
    unmapped = []
    aggregates: dict[str, dict] = {}
    aggregated_traditions = set()
    entries_raw = 0
    entries_aliased = 0
    collision_story_revisions = 0
    collision_character_entries = 0
    compared_story_revisions = 0

    for story in stories:
        identity = (story["story_id"], story["revision"])
        votes = decisions.get(identity, [])
        cited = {event_id for vote in votes for event_id in vote["evidence_event_ids"]}
        counts = defaultdict(int)
        characters = []
        for character in story["characters"]:
            assigned = _identity(story, character, mappings, canonicals)
            counts[assigned["analysis_id"]] += 1
            characters.append({
                "character_id": character["character_id"],
                "raw_name": character["name"],
                "sibling_order": character["sibling_order"],
                **assigned,
            })
        colliding_ids = sorted(key for key, count in counts.items() if count > 1)
        for character in characters:
            character["collision"] = character["analysis_id"] in colliding_ids
        if colliding_ids:
            collision_story_revisions += 1
            for analysis_id in colliding_ids:
                members = [item for item in characters if item["analysis_id"] == analysis_id]
                collision_character_entries += len(members)
                collision_ids = [item["character_id"] for item in members if item["character_id"]]
                kind = ("ambiguous_raw_name" if analysis_id.startswith("raw:")
                        else "merge_collision")
                collisions.append({
                    "story_id": story["story_id"], "revision": story["revision"],
                    "analysis_id": analysis_id, "display_name": members[0]["display_name"],
                    "kind": kind, "character_ids": collision_ids,
                    "raw_names": sorted({item["raw_name"] for item in members}),
                    "detail": ("同一修订内两个角色使用了相同原始称呼，无法判断其对应关系"
                               if kind == "ambiguous_raw_name" else
                               "同一修订内不同称呼被归并到同一规范人物，归并结果有歧义"),
                })
        else:
            compared_story_revisions += 1

        for character in characters:
            entries_raw += 1
            if character["basis"] == "alias":
                entries_aliased += 1
            else:
                unmapped.append({"tradition": story["tradition"], "raw_name": character["raw_name"],
                                 "story_id": story["story_id"], "revision": story["revision"],
                                 "character_id": character["character_id"]})
            if character["collision"]:
                continue
            aggregate = aggregates.setdefault(character["analysis_id"], {
                "analysis_id": character["analysis_id"],
                "display_name": character["display_name"],
                "basis": character["basis"],
                "canonical_id": character["canonical_id"],
                "alias_ids": set(),
                "raw_names": set(),
                "traditions": set(),
                "character_ids": set(),
                "story_revisions": 0,
                "events": 0,
                "cited_events": 0,
                "by_tradition": {},
            })
            aggregate["alias_ids"].add(character["alias_id"])
            aggregate["raw_names"].add(character["raw_name"])
            aggregate["traditions"].add(story["tradition"])
            aggregate["character_ids"].add(character["character_id"])
            aggregate["story_revisions"] += 1
            tradition_row = aggregate["by_tradition"].setdefault(story["tradition"], {
                "tradition": story["tradition"], "story_revisions": 0,
                "events": 0, "cited_events": 0, "raw_names": set(),
            })
            tradition_row["story_revisions"] += 1
            tradition_row["raw_names"].add(character["raw_name"])
            actor_events = [event for event in story["events"]
                            if event["actor_id"] == character["character_id"]]
            aggregate["events"] += len(actor_events)
            aggregate["cited_events"] += sum(event["event_id"] in cited for event in actor_events)
            tradition_row["events"] += len(actor_events)
            tradition_row["cited_events"] += sum(event["event_id"] in cited for event in actor_events)
            aggregated_traditions.add(story["tradition"])

        story_rows.append({
            "story_id": story["story_id"], "revision": story["revision"],
            "tradition": story["tradition"],
            "collision_analysis_ids": colliding_ids,
            "characters": [{key: value for key, value in character.items() if key != "alias_id"
                            or value is not None} for character in characters],
        })

    character_rows = []
    for aggregate in sorted(aggregates.values(), key=lambda item: item["analysis_id"]):
        by_tradition = []
        for tradition_row in sorted(aggregate["by_tradition"].values(), key=lambda item: item["tradition"]):
            events = tradition_row["events"]
            by_tradition.append({
                "tradition": tradition_row["tradition"],
                "story_revisions": tradition_row["story_revisions"],
                "raw_names": sorted(tradition_row["raw_names"]),
                "events": events,
                "cited_events": tradition_row["cited_events"],
                "event_coverage_rate": tradition_row["cited_events"] / events if events else None,
            })
        events = aggregate["events"]
        character_rows.append({
            "analysis_id": aggregate["analysis_id"],
            "display_name": aggregate["display_name"],
            "basis": aggregate["basis"],
            "canonical_id": aggregate["canonical_id"],
            "alias_ids": sorted(value for value in aggregate["alias_ids"] if value),
            "raw_names": sorted(aggregate["raw_names"]),
            "traditions": sorted(aggregate["traditions"]),
            "character_ids": sorted(aggregate["character_ids"]),
            "story_revisions": aggregate["story_revisions"],
            "events": events,
            "cited_events": aggregate["cited_events"],
            "event_coverage_rate": aggregate["cited_events"] / events if events else None,
            "by_tradition": by_tradition,
        })

    distinct_ids = set(aggregates) | {item["analysis_id"] for item in collisions}
    canonical_ids = {item["canonical_id"] for item in character_rows if item["canonical_id"]}
    return {
        "policy_id": policy["policy_id"],
        "policy": policy,
        "matching": "exact tradition, raw character name, and active alias; no case or script folding",
        "denominators": {
            "story_revisions": len(stories),
            "compared_story_revisions": compared_story_revisions,
            "collision_story_revisions": collision_story_revisions,
            "raw_character_entries": entries_raw,
            "aliased_character_entries": entries_aliased,
            "unmapped_character_entries": entries_raw - entries_aliased,
            "collision_character_entries": collision_character_entries,
            "analysed_character_entries": entries_raw - collision_character_entries,
            "distinct_analysis_characters": len(distinct_ids),
            "compared_analysis_characters": len(character_rows),
            "distinct_canonical_characters_observed": len(canonical_ids),
            "traditions_with_character_data": len(aggregated_traditions),
        },
        "coverage": {
            "by_raw_entry": {
                "aliased": entries_aliased / entries_raw if entries_raw else None,
                "unmapped": (entries_raw - entries_aliased) / entries_raw if entries_raw else None,
                "collision": collision_character_entries / entries_raw if entries_raw else None,
            },
            "by_story_revision": {
                "comparable": (compared_story_revisions / len(stories)) if stories else None,
                "with_collision": (collision_story_revisions / len(stories)) if stories else None,
            },
        },
        "characters": character_rows,
        "unmapped": unmapped,
        "collisions": collisions,
        "stories": story_rows,
        "interpretation_limit": (
            "Character totals are an analysis overlay. Raw names, story text and reader decisions "
            "remain unchanged; unresolved names and within-story collisions are excluded from "
            "canonical aggregates rather than silently assigned."
        ),
    }


def compare_canonical_characters(view: dict) -> dict:
    """把同一规范人物在不同传统中的出现分别列出，不做跨传统显著性推断。"""
    rows = []
    for character in view["characters"]:
        if character["canonical_id"] is None:
            continue
        rows.append({
            "canonical_id": character["canonical_id"],
            "canonical_name": character["display_name"],
            "raw_names": character["raw_names"],
            "total_story_revisions": character["story_revisions"],
            "total_events": character["events"],
            "total_cited_events": character["cited_events"],
            "event_coverage_rate": character["event_coverage_rate"],
            "traditions": character["by_tradition"],
        })
    return {
        "basis": "canonical character aggregates; colliding story revisions are excluded",
        "characters": rows,
        "unmapped_names": sorted(
            {(item["tradition"], item["raw_name"]) for item in view["unmapped"]}
        ),
        "collisions": view["collisions"],
        "interpretation_limit": (
            "Canonical character rows describe the linked sample only. Denominators are story "
            "revisions and events, not the source traditions' full corpora."
        ),
    }
