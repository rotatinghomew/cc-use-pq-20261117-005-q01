from __future__ import annotations

from story_patterns.catalog.validate import _text, digest_json
from story_patterns.errors import ConflictError, NotFoundError, ValidationError

ACTIVE = "active"
REVOKED = "revoked"


def canonical_payload(canonical_id: str, canonical_name: str, created_by: str) -> dict:
    return {"canonical_id": canonical_id, "canonical_name": canonical_name, "created_by": created_by}


def alias_payload(alias: dict) -> dict:
    payload = {
        "alias_id": alias["alias_id"],
        "tradition": alias["tradition"],
        "raw_name": alias["raw_name"],
        "canonical_id": alias["canonical_id"],
        "rationale": alias["rationale"],
        "created_by": alias["created_by"],
        "status": alias["status"],
    }
    if alias["status"] == REVOKED:
        payload["revoked_by"] = alias["revoked_by"]
        payload["revocation_reason"] = alias["revocation_reason"]
    return payload


def normalize_canonical(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("canonical character must be an object")
    return canonical_payload(
        _text(raw.get("canonical_id"), "canonical_id", 100),
        _text(raw.get("canonical_name"), "canonical_name", 120),
        _text(raw.get("created_by"), "created_by", 120),
    )


def normalize_alias(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("character alias must be an object")
    return {
        "alias_id": _text(raw.get("alias_id"), "alias_id", 120),
        "tradition": _text(raw.get("tradition"), "tradition", 120),
        "raw_name": _text(raw.get("raw_name"), "raw_name", 120),
        "canonical_id": _text(raw.get("canonical_id"), "canonical_id", 100),
        "rationale": _text(raw.get("rationale"), "rationale", 1000),
        "created_by": _text(raw.get("created_by"), "created_by", 120),
        "status": ACTIVE,
        "revoked_by": None,
        "revocation_reason": None,
    }


def normalize_revocation(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("alias revocation must be an object")
    return {
        "alias_id": _text(raw.get("alias_id"), "alias_id", 120),
        "revoked_by": _text(raw.get("revoked_by"), "revoked_by", 120),
        "revocation_reason": _text(raw.get("revocation_reason"), "revocation_reason", 1000),
    }


def policy_document(canonicals: list[dict], aliases: list[dict]) -> dict:
    """按传统限定的人物归并口径，可确定地序列化以便报告引用。"""
    active = [item for item in aliases if item["status"] == ACTIVE]
    referenced = {item["canonical_id"] for item in active}
    return {
        "canonicals": [
            {"canonical_id": item["canonical_id"], "canonical_name": item["canonical_name"]}
            for item in sorted(canonicals, key=lambda value: value["canonical_id"])
            if item["canonical_id"] in referenced
        ],
        "mappings": [
            {
                "tradition": item["tradition"],
                "raw_name": item["raw_name"],
                "canonical_id": item["canonical_id"],
                "alias_id": item["alias_id"],
            }
            for item in sorted(active, key=lambda value: (value["tradition"], value["raw_name"]))
        ],
    }


def policy_snapshot(canonicals: list[dict], aliases: list[dict]) -> dict:
    document = policy_document(canonicals, aliases)
    return {"policy_id": digest_json(document), **document}


def empty_policy() -> dict:
    return policy_snapshot([], [])


class AliasService:
    def __init__(self, store):
        self.store = store

    def record(self, raw: dict) -> dict:
        canonical = normalize_canonical(raw)
        alias = normalize_alias(raw)
        alias_digest = digest_json(alias_payload(alias))
        with self.store.transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM character_aliases WHERE alias_id=?", (alias["alias_id"],)
            ).fetchone()
            if previous:
                if previous["digest"] == alias_digest and previous["status"] == ACTIVE:
                    return {"alias_id": alias["alias_id"], "status": "already_recorded",
                            "canonical_id": previous["canonical_id"]}
                raise ConflictError(
                    f"alias {alias['alias_id']} already identifies different or revoked content"
                )
            existing_canonical = connection.execute(
                "SELECT * FROM canonical_characters WHERE canonical_id=?", (canonical["canonical_id"],)
            ).fetchone()
            if existing_canonical:
                if existing_canonical["canonical_name"] != canonical["canonical_name"]:
                    raise ConflictError(
                        f"canonical character {canonical['canonical_id']} already has different content"
                    )
                duplicate_name = connection.execute(
                    "SELECT canonical_id FROM canonical_characters "
                    "WHERE canonical_name=? AND canonical_id<>?",
                    (canonical["canonical_name"], canonical["canonical_id"]),
                ).fetchone()
                if duplicate_name:
                    raise ConflictError(
                        f"canonical name {canonical['canonical_name']!r} is already used by "
                        f"{duplicate_name['canonical_id']}"
                    )
            else:
                duplicate_name = connection.execute(
                    "SELECT canonical_id FROM canonical_characters WHERE canonical_name=?",
                    (canonical["canonical_name"],),
                ).fetchone()
                if duplicate_name:
                    raise ConflictError(
                        f"canonical name {canonical['canonical_name']!r} is already used by "
                        f"{duplicate_name['canonical_id']}"
                    )
                connection.execute(
                    "INSERT INTO canonical_characters(canonical_id, canonical_name, created_by, digest) "
                    "VALUES(?,?,?,?)",
                    (canonical["canonical_id"], canonical["canonical_name"],
                     canonical["created_by"], digest_json(canonical_payload(
                         canonical["canonical_id"], canonical["canonical_name"], canonical["created_by"]))),
                )
            active = connection.execute(
                "SELECT alias_id, canonical_id FROM character_aliases "
                "WHERE tradition=? AND raw_name=? AND status='active'",
                (alias["tradition"], alias["raw_name"]),
            ).fetchone()
            if active:
                raise ConflictError(
                    f"raw name {alias['raw_name']!r} in tradition {alias['tradition']!r} is already "
                    f"mapped to {active['canonical_id']} by alias {active['alias_id']}"
                )
            connection.execute(
                "INSERT INTO character_aliases(alias_id, tradition, raw_name, canonical_id, rationale, "
                "created_by, status, revoked_by, revocation_reason, digest) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (alias["alias_id"], alias["tradition"], alias["raw_name"], alias["canonical_id"],
                 alias["rationale"], alias["created_by"], ACTIVE, None, None, alias_digest),
            )
        return {"alias_id": alias["alias_id"], "status": "recorded",
                "canonical_id": alias["canonical_id"], "tradition": alias["tradition"],
                "raw_name": alias["raw_name"]}

    def revoke(self, raw: dict) -> dict:
        revocation = normalize_revocation(raw)
        with self.store.transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM character_aliases WHERE alias_id=?", (revocation["alias_id"],)
            ).fetchone()
            if previous is None:
                raise NotFoundError(f"alias {revocation['alias_id']!r} was not found")
            if previous["status"] == REVOKED:
                if (previous["revoked_by"] == revocation["revoked_by"]
                        and previous["revocation_reason"] == revocation["revocation_reason"]):
                    return {"alias_id": revocation["alias_id"], "status": "already_revoked"}
                raise ConflictError(
                    f"alias {revocation['alias_id']} was already revoked with a different record"
                )
            revoked = dict(previous) | {
                "status": REVOKED,
                "revoked_by": revocation["revoked_by"],
                "revocation_reason": revocation["revocation_reason"],
            }
            connection.execute(
                "UPDATE character_aliases SET status=?, revoked_by=?, revocation_reason=?, digest=? "
                "WHERE alias_id=?",
                (REVOKED, revocation["revoked_by"], revocation["revocation_reason"],
                 digest_json(alias_payload(revoked)), revocation["alias_id"]),
            )
        return {"alias_id": revocation["alias_id"], "status": "revoked",
                "canonical_id": previous["canonical_id"], "tradition": previous["tradition"],
                "raw_name": previous["raw_name"]}

    def listing(self, tradition: str | None = None, include_revoked: bool = True) -> dict:
        if tradition is not None and (not isinstance(tradition, str) or not tradition.strip()):
            raise ValidationError("tradition filter must be a non-empty label or omitted")
        canonicals, aliases = self.store.character_alias_history(tradition, include_revoked)
        active = [item for item in aliases if item["status"] == ACTIVE]
        return {
            "canonicals": canonicals,
            "aliases": aliases,
            "active_mapping_count": len(active),
            "revoked_mapping_count": sum(item["status"] == REVOKED for item in aliases),
            "policy": policy_snapshot(canonicals, aliases),
            "matching": "exact tradition and raw character name; no case or script folding",
        }

    def policy(self) -> dict:
        canonicals, aliases = self.store.character_alias_snapshot()
        return policy_snapshot(canonicals, aliases)
