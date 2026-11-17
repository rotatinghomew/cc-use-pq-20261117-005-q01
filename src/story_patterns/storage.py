from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS import_batches (
    batch_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    story_count INTEGER NOT NULL CHECK(story_count > 0)
);
CREATE TABLE IF NOT EXISTS stories (
    story_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    title TEXT NOT NULL,
    tradition TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY(story_id, revision)
);
CREATE INDEX IF NOT EXISTS stories_by_tradition ON stories(tradition, story_id, revision);
CREATE TABLE IF NOT EXISTS batch_stories (
    batch_id TEXT NOT NULL REFERENCES import_batches(batch_id) ON DELETE RESTRICT,
    story_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY(batch_id, story_id, revision),
    FOREIGN KEY(story_id, revision) REFERENCES stories(story_id, revision) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS batch_stories_by_story ON batch_stories(story_id, revision, batch_id);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    story_revision INTEGER NOT NULL,
    annotator TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    pattern TEXT NOT NULL,
    youngest_outcome TEXT NOT NULL,
    evidence_event_ids TEXT NOT NULL,
    note TEXT NOT NULL,
    digest TEXT NOT NULL,
    UNIQUE(story_id, story_revision, annotator, sequence),
    FOREIGN KEY(story_id, story_revision) REFERENCES stories(story_id, revision)
);
CREATE INDEX IF NOT EXISTS decisions_by_story ON decisions(story_id, story_revision, annotator, sequence);
CREATE TABLE IF NOT EXISTS adjudications (
    adjudication_id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    story_revision INTEGER NOT NULL,
    moderator TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    pattern TEXT NOT NULL,
    youngest_outcome TEXT NOT NULL,
    evidence_event_ids TEXT NOT NULL,
    rationale TEXT NOT NULL,
    digest TEXT NOT NULL,
    UNIQUE(story_id, story_revision, sequence),
    FOREIGN KEY(story_id, story_revision) REFERENCES stories(story_id, revision)
);
CREATE INDEX IF NOT EXISTS adjudications_by_story ON adjudications(story_id, story_revision, sequence);
CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canonical_characters (
    canonical_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    digest TEXT NOT NULL,
    UNIQUE(canonical_name)
);
CREATE TABLE IF NOT EXISTS character_aliases (
    alias_id TEXT PRIMARY KEY,
    tradition TEXT NOT NULL,
    raw_name TEXT NOT NULL,
    canonical_id TEXT NOT NULL REFERENCES canonical_characters(canonical_id) ON DELETE RESTRICT,
    rationale TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'revoked')),
    revoked_by TEXT,
    revocation_reason TEXT,
    digest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS aliases_by_tradition ON character_aliases(tradition, raw_name, alias_id);
CREATE INDEX IF NOT EXISTS aliases_by_canonical ON character_aliases(canonical_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_alias_per_name
    ON character_aliases(tradition, raw_name) WHERE status = 'active';
"""


class StoryStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("a file-backed database is required for durable local reports")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        connection = self.connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read_connection(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def story_document(self, story_id: str, revision: int) -> dict | None:
        with self.read_connection() as connection:
            row = connection.execute(
                "SELECT document FROM stories WHERE story_id=? AND revision=?", (story_id, revision)
            ).fetchone()
        return json.loads(row["document"]) if row else None

    def list_stories(self, tradition: str | None = None) -> list[dict]:
        with self.read_connection() as connection:
            if tradition is None:
                rows = connection.execute(
                    "SELECT document FROM stories ORDER BY tradition, story_id, revision"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT document FROM stories WHERE tradition=? ORDER BY story_id, revision",
                    (tradition,),
                ).fetchall()
        return [json.loads(row["document"]) for row in rows]

    def latest_decisions(self, story_id: str, revision: int) -> list[dict]:
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT d.* FROM decisions d JOIN ("
                "SELECT annotator, MAX(sequence) AS latest FROM decisions "
                "WHERE story_id=? AND story_revision=? GROUP BY annotator"
                ") newest ON d.annotator=newest.annotator AND d.sequence=newest.latest "
                "WHERE d.story_id=? AND d.story_revision=? ORDER BY d.annotator",
                (story_id, revision, story_id, revision),
            ).fetchall()
        return [dict(row) | {"evidence_event_ids": json.loads(row["evidence_event_ids"])} for row in rows]

    def decision_history(self, story_id: str, revision: int) -> tuple[list[dict], list[dict]]:
        with self.read_connection() as connection:
            votes = connection.execute(
                "SELECT * FROM decisions WHERE story_id=? AND story_revision=? ORDER BY annotator, sequence",
                (story_id, revision),
            ).fetchall()
            adjudications = connection.execute(
                "SELECT * FROM adjudications WHERE story_id=? AND story_revision=? ORDER BY sequence",
                (story_id, revision),
            ).fetchall()
        return (
            [dict(row) | {"evidence_event_ids": json.loads(row["evidence_event_ids"])} for row in votes],
            [dict(row) | {"evidence_event_ids": json.loads(row["evidence_event_ids"])} for row in adjudications],
        )

    def latest_decisions_for_stories(self, identities: list[tuple[str, int]]) -> dict[tuple[str, int], list[dict]]:
        result = {}
        for story_id, revision in identities:
            result[(story_id, revision)] = self.latest_decisions(story_id, revision)
        return result

    def analysis_snapshot(self, tradition: str | None) -> tuple[list[dict], dict, dict, dict]:
        with self.read_connection() as connection:
            connection.execute("BEGIN")
            stories, decisions, origins, adjudications = self._analysis_rows(connection, tradition)
        return stories, decisions, origins, adjudications

    def analysis_snapshot_with_aliases(self, tradition: str | None) -> tuple[list[dict], dict, dict, dict,
                                                                           list[dict], list[dict]]:
        with self.read_connection() as connection:
            connection.execute("BEGIN")
            stories, decisions, origins, adjudications = self._analysis_rows(connection, tradition)
            canonicals = [dict(row) for row in connection.execute(
                "SELECT canonical_id, canonical_name, created_by FROM canonical_characters "
                "ORDER BY canonical_id"
            )]
            aliases = [dict(row) for row in connection.execute(
                "SELECT * FROM character_aliases ORDER BY tradition, raw_name, alias_id"
            )]
        return stories, decisions, origins, adjudications, canonicals, aliases

    def _analysis_rows(self, connection, tradition: str | None):
        if tradition is None:
            rows = connection.execute(
                "SELECT document FROM stories ORDER BY tradition, story_id, revision"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT document FROM stories WHERE tradition=? ORDER BY story_id, revision", (tradition,)
            ).fetchall()
        stories = [json.loads(row["document"]) for row in rows]
        identities = [(item["story_id"], item["revision"]) for item in stories]
        decisions = {}
        adjudications = {}
        for story_id, revision in identities:
            latest = connection.execute(
                "SELECT d.* FROM decisions d JOIN ("
                "SELECT annotator, MAX(sequence) AS latest FROM decisions "
                "WHERE story_id=? AND story_revision=? GROUP BY annotator"
                ") newest ON d.annotator=newest.annotator AND d.sequence=newest.latest "
                "WHERE d.story_id=? AND d.story_revision=? ORDER BY d.annotator",
                (story_id, revision, story_id, revision),
            ).fetchall()
            decisions[(story_id, revision)] = [
                dict(row) | {"evidence_event_ids": json.loads(row["evidence_event_ids"])} for row in latest
            ]
            adjudication = connection.execute(
                "SELECT * FROM adjudications WHERE story_id=? AND story_revision=? "
                "ORDER BY sequence DESC LIMIT 1", (story_id, revision)
            ).fetchone()
            adjudications[(story_id, revision)] = (
                dict(adjudication) | {"evidence_event_ids": json.loads(adjudication["evidence_event_ids"])}
                if adjudication else None
            )
        origins = {identity: [] for identity in identities}
        if identities:
            rows = connection.execute(
                "SELECT batch_id, story_id, revision FROM batch_stories ORDER BY batch_id"
            ).fetchall()
            for row in rows:
                identity = (row["story_id"], row["revision"])
                if identity in origins:
                    origins[identity].append(row["batch_id"])
        return stories, decisions, origins, adjudications

    def catalog_manifest(self) -> tuple[list[dict], list[dict]]:
        with self.read_connection() as connection:
            batches = connection.execute(
                "SELECT b.batch_id, b.digest, b.story_count, COUNT(bs.story_id) AS linked_story_count "
                "FROM import_batches b LEFT JOIN batch_stories bs ON bs.batch_id=b.batch_id "
                "GROUP BY b.batch_id ORDER BY b.batch_id"
            ).fetchall()
            stories = connection.execute(
                "SELECT document FROM stories ORDER BY story_id, revision"
            ).fetchall()
        return [dict(row) for row in batches], [json.loads(row["document"]) for row in stories]

    def catalog_audit_snapshot(self) -> dict:
        with self.read_connection() as connection:
            return {
                "stories": [dict(row) for row in connection.execute("SELECT * FROM stories ORDER BY story_id, revision")],
                "batches": [dict(row) for row in connection.execute("SELECT * FROM import_batches ORDER BY batch_id")],
                "batch_stories": [dict(row) for row in connection.execute(
                    "SELECT * FROM batch_stories ORDER BY batch_id, story_id, revision")],
                "decisions": [dict(row) for row in connection.execute("SELECT * FROM decisions ORDER BY decision_id")],
                "adjudications": [dict(row) for row in connection.execute(
                    "SELECT * FROM adjudications ORDER BY adjudication_id")],
                "reports": [dict(row) for row in connection.execute("SELECT * FROM reports ORDER BY report_id")],
                "canonical_characters": [dict(row) for row in connection.execute(
                    "SELECT * FROM canonical_characters ORDER BY canonical_id")],
                "character_aliases": [dict(row) for row in connection.execute(
                    "SELECT * FROM character_aliases ORDER BY alias_id")],
            }

    def decision_records(self, decision_ids: list[str]) -> dict[str, dict]:
        if not decision_ids:
            return {}
        placeholders = ",".join("?" for _ in decision_ids)
        with self.read_connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM decisions WHERE decision_id IN ({placeholders})", decision_ids
            ).fetchall()
        return {row["decision_id"]: dict(row) | {"evidence_event_ids": json.loads(row["evidence_event_ids"])}
                for row in rows}

    def adjudication_records(self, adjudication_ids: list[str]) -> dict[str, dict]:
        if not adjudication_ids:
            return {}
        placeholders = ",".join("?" for _ in adjudication_ids)
        with self.read_connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM adjudications WHERE adjudication_id IN ({placeholders})", adjudication_ids
            ).fetchall()
        return {row["adjudication_id"]: dict(row) | {"evidence_event_ids": json.loads(row["evidence_event_ids"])}
                for row in rows}

    def latest_decision_ids(self, identities: list[tuple[str, int]]) -> set[str]:
        result = set()
        for story_id, revision in identities:
            result.update(item["decision_id"] for item in self.latest_decisions(story_id, revision))
        return result

    def latest_adjudication_ids(self, identities: list[tuple[str, int]]) -> set[str]:
        result = set()
        for story_id, revision in identities:
            with self.read_connection() as connection:
                row = connection.execute(
                    "SELECT adjudication_id FROM adjudications WHERE story_id=? AND story_revision=? "
                    "ORDER BY sequence DESC LIMIT 1", (story_id, revision)
                ).fetchone()
            if row:
                result.add(row["adjudication_id"])
        return result

    def report_document(self, report_id: str) -> dict | None:
        with self.read_connection() as connection:
            row = connection.execute("SELECT document FROM reports WHERE report_id=?", (report_id,)).fetchone()
        return json.loads(row["document"]) if row else None

    def character_alias_snapshot(self, tradition: str | None = None) -> tuple[list[dict], list[dict]]:
        with self.read_connection() as connection:
            canonicals = connection.execute(
                "SELECT canonical_id, canonical_name, created_by FROM canonical_characters "
                "ORDER BY canonical_id"
            ).fetchall()
            if tradition is None:
                aliases = connection.execute(
                    "SELECT * FROM character_aliases ORDER BY tradition, raw_name, alias_id"
                ).fetchall()
            else:
                aliases = connection.execute(
                    "SELECT * FROM character_aliases WHERE tradition=? ORDER BY raw_name, alias_id",
                    (tradition,),
                ).fetchall()
        return [dict(row) for row in canonicals], [dict(row) for row in aliases]

    def character_alias_history(self, tradition: str | None = None,
                                include_revoked: bool = True) -> tuple[list[dict], list[dict]]:
        canonicals, aliases = self.character_alias_snapshot(tradition)
        if tradition is not None:
            referenced = {item["canonical_id"] for item in aliases}
            canonicals = [item for item in canonicals if item["canonical_id"] in referenced]
        if not include_revoked:
            aliases = [item for item in aliases if item["status"] == "active"]
        return canonicals, aliases

    def integrity_issues(self) -> list[str]:
        with self.read_connection() as connection:
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        issues = [f"foreign key violation: {tuple(row)}" for row in foreign_keys]
        if quick_check != "ok":
            issues.append(f"SQLite quick_check: {quick_check}")
        return issues
