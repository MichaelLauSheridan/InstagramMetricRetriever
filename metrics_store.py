"""Local SQLite storage and metric parsing for the Instagram metrics app."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


APP_HOME = Path(__file__).resolve().parent / "app_data"
METRIC_FIELDS = (
    "views",
    "viewers",
    "accounts_reached",
    "reported_interactions",
    "likes",
    "comments",
    "saves",
    "shares",
    "accounts_engaged",
    "follower_views_pct",
    "non_follower_views_pct",
    "views_home",
    "views_profile",
    "views_other",
    "profile_activity",
    "profile_visits",
    "external_link_taps",
    "business_address_taps",
    "follows",
)
INTEGER_FIELDS = set(METRIC_FIELDS) - {
    "follower_views_pct",
    "non_follower_views_pct",
}
LABELS = {
    "views": ("views", "video views"),
    "viewers": ("viewers",),
    "accounts_reached": ("accounts reached", "reach"),
    "reported_interactions": ("interactions",),
    "likes": ("likes",),
    "comments": ("comments",),
    "saves": ("saves",),
    "shares": ("shares",),
    "accounts_engaged": ("accounts engaged",),
    "follower_views_pct": ("followers",),
    "non_follower_views_pct": ("non-followers", "nonfollowers"),
    "views_home": ("from home", "home"),
    "views_profile": ("from profile", "profile"),
    "views_other": ("from other", "other"),
    "profile_activity": ("profile activity",),
    "profile_visits": ("profile visits",),
    "external_link_taps": ("external link taps", "link taps"),
    "business_address_taps": ("business address taps", "address taps"),
    "follows": ("follows",),
}


@dataclass(frozen=True)
class ParsedMetric:
    value: int | float | None
    raw: str
    rounded: bool


def parse_metric_value(raw: Any, *, percentage: bool = False) -> ParsedMetric:
    """Parse a displayed metric without turning unavailable values into zero."""
    original = "" if raw is None else str(raw).strip()
    if not original or original.casefold() in {"--", "—", "n/a", "na", "unavailable"}:
        return ParsedMetric(None, original, False)

    text = original.replace(",", "").replace(" ", "").replace("%", "")
    multiplier = 1
    rounded = False
    if text[-1:].casefold() in {"k", "m"}:
        multiplier = 1_000 if text[-1].casefold() == "k" else 1_000_000
        text = text[:-1]
        rounded = True
    try:
        value = float(text) * multiplier
    except ValueError as exc:
        raise ValueError(f"Unrecognized metric value: {original!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"Metric must be finite: {original!r}")
    if percentage:
        if not 0 <= value <= 100:
            raise ValueError("Percentage must be between 0 and 100.")
        return ParsedMetric(value, original, rounded)
    if value < 0:
        raise ValueError("Metrics cannot be negative.")
    if value.is_integer():
        return ParsedMetric(int(value), original, rounded)
    return ParsedMetric(value, original, rounded)


def calculate_metrics(metrics: dict[str, Any]) -> dict[str, int | float | None]:
    interactions = [metrics.get(k) for k in ("likes", "comments", "saves", "shares")]
    itemized = sum(interactions) if all(value is not None for value in interactions) else None
    views = [metrics.get(k) for k in ("views_home", "views_profile", "views_other")]
    sources = sum(views) if all(value is not None for value in views) else None
    reported = metrics.get("reported_interactions")
    return {
        "itemized_interactions": itemized,
        "interaction_difference": reported - itemized if reported is not None and itemized is not None else None,
        "view_sources_total": sources,
    }


def extract_suggestions(raw_text: str) -> dict[str, str]:
    """Return conservative label/value OCR suggestions; never guesses dates."""
    suggestions: dict[str, str] = {}
    for line in raw_text.splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        if not normalized:
            continue
        for field, labels in LABELS.items():
            if field in suggestions:
                continue
            for label in sorted(labels, key=len, reverse=True):
                match = re.search(
                    rf"(?i)(?:^|{re.escape(label)})\s*[:\-]?\s*([0-9][0-9,.]*\s*[kKmM]?%?|--)\s*$",
                    normalized,
                )
                if match:
                    if field in {"follower_views_pct", "non_follower_views_pct"} and not match.group(1).endswith("%"):
                        continue
                    suggestions[field] = match.group(1).strip()
                    break
    return suggestions


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else APP_HOME / "instagram_metrics.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    initialize(connection)
    return connection


def initialize(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY,
            handle TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            label TEXT NOT NULL,
            captured_at TEXT,
            metric_basis TEXT NOT NULL DEFAULT 'unknown'
                CHECK(metric_basis IN ('lifetime','selected period','unknown')),
            period_start TEXT,
            period_end TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(account_id, label)
        );
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            sha256 TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            path TEXT NOT NULL,
            kind TEXT NOT NULL,
            raw_text TEXT,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS post_records (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
            content_id TEXT NOT NULL,
            post_url TEXT,
            title TEXT,
            publication_date TEXT,
            content_type TEXT NOT NULL DEFAULT 'other',
            metric_basis TEXT NOT NULL DEFAULT 'unknown',
            period_start TEXT,
            period_end TEXT,
            notes TEXT,
            paid_organic TEXT,
            metrics_json TEXT NOT NULL,
            raw_values_json TEXT NOT NULL,
            rounded_fields_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'reviewed',
            imported_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(account_id, content_id, snapshot_id)
        );
        CREATE TABLE IF NOT EXISTS record_sources (
            record_id INTEGER NOT NULL REFERENCES post_records(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            PRIMARY KEY(record_id, source_id)
        );
        CREATE TABLE IF NOT EXISTS account_snapshots (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
            followers_total INTEGER,
            metrics_json TEXT NOT NULL,
            raw_values_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'reviewed',
            UNIQUE(account_id, snapshot_id)
        );
        CREATE TABLE IF NOT EXISTS edit_history (
            id INTEGER PRIMARY KEY,
            record_id INTEGER NOT NULL REFERENCES post_records(id) ON DELETE CASCADE,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            edited_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_drafts (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
            content_id TEXT NOT NULL,
            fields_json TEXT NOT NULL,
            raw_values_json TEXT NOT NULL,
            source_ids_json TEXT NOT NULL,
            raw_text TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    post_columns = {row["name"] for row in db.execute("PRAGMA table_info(post_records)")}
    if "imported_at" not in post_columns:
        db.execute("ALTER TABLE post_records ADD COLUMN imported_at TEXT NOT NULL DEFAULT ''")
    db.execute(
        "UPDATE post_records SET imported_at=created_at WHERE imported_at IS NULL OR imported_at=''"
    )
    db.commit()


def add_account(db: sqlite3.Connection, handle: str) -> int:
    normalized = handle.strip().lstrip("@")
    if not normalized:
        raise ValueError("Enter an account handle.")
    cursor = db.execute(
        "INSERT INTO accounts(handle, created_at) VALUES (?, ?)",
        (normalized, datetime.now().astimezone().isoformat(timespec="seconds")),
    )
    db.commit()
    return int(cursor.lastrowid)


def add_snapshot(
    db: sqlite3.Connection,
    account_id: int,
    label: str,
    captured_at: str | None,
    metric_basis: str,
    period_start: str | None = None,
    period_end: str | None = None,
) -> int:
    if not label.strip():
        raise ValueError("Enter a snapshot ID or label.")
    cursor = db.execute(
        """INSERT INTO snapshots
           (account_id,label,captured_at,metric_basis,period_start,period_end,created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            account_id,
            label.strip(),
            captured_at or None,
            metric_basis,
            period_start or None,
            period_end or None,
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def store_source(
    db: sqlite3.Connection,
    filename: str,
    data: bytes,
    kind: str,
    raw_text: str | None = None,
    storage_dir: str | Path | None = None,
) -> tuple[int | None, bool]:
    """Store an unchanged local copy. Return (source id, duplicate)."""
    digest = hashlib.sha256(data).hexdigest()
    existing = db.execute("SELECT id FROM sources WHERE sha256 = ?", (digest,)).fetchone()
    if existing:
        return int(existing["id"]), True
    imports_dir = Path(storage_dir) if storage_dir else APP_HOME / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name) or "import"
    destination = imports_dir / f"{digest[:12]}_{safe_name}"
    destination.write_bytes(data)
    cursor = db.execute(
        """INSERT INTO sources(sha256,filename,path,kind,raw_text,imported_at)
           VALUES(?,?,?,?,?,?)""",
        (
            digest,
            Path(filename).name,
            str(destination),
            kind,
            raw_text,
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    return int(cursor.lastrowid), False


def add_pending_draft(
    db: sqlite3.Connection,
    *,
    account_id: int,
    snapshot_id: int,
    content_id: str,
    fields: dict[str, Any],
    raw_values: dict[str, str],
    source_ids: Iterable[int],
    raw_text: str = "",
) -> int:
    cursor = db.execute(
        """INSERT INTO pending_drafts
           (account_id,snapshot_id,content_id,fields_json,raw_values_json,source_ids_json,raw_text,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            account_id, snapshot_id, content_id, json.dumps(fields, ensure_ascii=False),
            json.dumps(raw_values, ensure_ascii=False), json.dumps(sorted(set(source_ids))),
            raw_text, datetime.now().astimezone().isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def list_pending_drafts(db: sqlite3.Connection) -> list[dict[str, Any]]:
    drafts = []
    for row in db.execute(
        """SELECT d.*,a.handle,s.label AS snapshot_label
           FROM pending_drafts d JOIN accounts a ON a.id=d.account_id
           JOIN snapshots s ON s.id=d.snapshot_id ORDER BY d.created_at"""
    ):
        draft = dict(row)
        draft["fields"] = json.loads(draft.pop("fields_json"))
        draft["raw_values"] = json.loads(draft.pop("raw_values_json"))
        draft["source_ids"] = json.loads(draft.pop("source_ids_json"))
        drafts.append(draft)
    return drafts


def delete_pending_draft(db: sqlite3.Connection, draft_id: int) -> None:
    db.execute("DELETE FROM pending_drafts WHERE id=?", (draft_id,))
    db.commit()


def save_post_record(
    db: sqlite3.Connection,
    *,
    account_id: int,
    snapshot_id: int,
    content_id: str,
    fields: dict[str, Any],
    metrics: dict[str, int | float | None],
    raw_values: dict[str, str],
    rounded_fields: Iterable[str],
    source_ids: Iterable[int] = (),
    status: str = "reviewed",
    imported_at: str | None = None,
    record_id: int | None = None,
) -> int:
    content_id = content_id.strip()
    if not content_id:
        raise ValueError("A stable post/content ID is required.")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    serialized_metrics = json.dumps(metrics, ensure_ascii=False)
    serialized_raw = json.dumps(raw_values, ensure_ascii=False)
    serialized_rounded = json.dumps(sorted(set(rounded_fields)))

    db.execute("BEGIN")
    try:
        duplicate = db.execute(
            """SELECT id FROM post_records
               WHERE account_id=? AND content_id=? AND snapshot_id=? AND id != COALESCE(?, -1)""",
            (account_id, content_id, snapshot_id, record_id),
        ).fetchone()
        if duplicate:
            raise ValueError("A record for this account, post, and snapshot already exists.")

        if record_id is None:
            cursor = db.execute(
                """INSERT INTO post_records
                   (account_id,snapshot_id,content_id,post_url,title,publication_date,content_type,
                    metric_basis,period_start,period_end,notes,paid_organic,metrics_json,
                    raw_values_json,rounded_fields_json,status,imported_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, snapshot_id, content_id, fields.get("post_url"), fields.get("title"),
                    fields.get("publication_date"), fields.get("content_type", "other"),
                    fields.get("metric_basis", "unknown"), fields.get("period_start"),
                    fields.get("period_end"), fields.get("notes"), fields.get("paid_organic"),
                    serialized_metrics, serialized_raw, serialized_rounded, status,
                    imported_at or now, now, now,
                ),
            )
            record_id = int(cursor.lastrowid)
            for key, original in raw_values.items():
                parsed_original = _parse_or_none(original, key in {"follower_views_pct", "non_follower_views_pct"})
                current = metrics.get(key)
                if parsed_original is not None and current is not None and parsed_original != current:
                    _insert_history(db, record_id, key, parsed_original, current)
        else:
            previous = db.execute(
                "SELECT metrics_json FROM post_records WHERE id=?", (record_id,)
            ).fetchone()
            if not previous:
                raise ValueError("Record no longer exists.")
            old_metrics = json.loads(previous["metrics_json"])
            for field in METRIC_FIELDS:
                if old_metrics.get(field) != metrics.get(field):
                    _insert_history(db, record_id, field, old_metrics.get(field), metrics.get(field))
            db.execute(
                """UPDATE post_records SET
                   content_id=?,post_url=?,title=?,publication_date=?,content_type=?,metric_basis=?,
                period_start=?,period_end=?,notes=?,paid_organic=?,metrics_json=?,status=?,
                updated_at=? WHERE id=?""",
                (
                    content_id, fields.get("post_url"), fields.get("title"),
                    fields.get("publication_date"), fields.get("content_type", "other"),
                    fields.get("metric_basis", "unknown"), fields.get("period_start"),
                    fields.get("period_end"), fields.get("notes"), fields.get("paid_organic"),
                 serialized_metrics, status, now, record_id,
                ),
            )
            db.execute("DELETE FROM record_sources WHERE record_id=?", (record_id,))

        for source_id in set(source_ids):
            db.execute(
                "INSERT OR IGNORE INTO record_sources(record_id,source_id) VALUES(?,?)",
                (record_id, source_id),
            )
        db.commit()
        return record_id
    except Exception:
        db.rollback()
        raise


def _insert_history(db: sqlite3.Connection, record_id: int, field: str, old: Any, new: Any) -> None:
    db.execute(
        "INSERT INTO edit_history(record_id,field_name,old_value,new_value,edited_at) VALUES(?,?,?,?,?)",
        (record_id, field, _json_value(old), _json_value(new), datetime.now().astimezone().isoformat(timespec="seconds")),
    )


def _json_value(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _parse_or_none(raw: str, percentage: bool) -> int | float | None:
    try:
        return parse_metric_value(raw, percentage=percentage).value
    except ValueError:
        return None


def list_records(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT p.*,a.handle,s.label AS snapshot_label,s.captured_at,s.metric_basis AS snapshot_basis,
                  s.period_start AS snapshot_period_start,s.period_end AS snapshot_period_end,
                  (SELECT group_concat(src.filename, ', ')
                   FROM record_sources rs JOIN sources src ON src.id=rs.source_id
                   WHERE rs.record_id=p.id) AS source_filenames
           FROM post_records p JOIN accounts a ON a.id=p.account_id
           JOIN snapshots s ON s.id=p.snapshot_id
           ORDER BY p.updated_at DESC"""
    ).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        record["metrics"] = json.loads(record.pop("metrics_json"))
        record["import_datetime"] = record["imported_at"] or record["created_at"]
        record["raw_values"] = json.loads(record.pop("raw_values_json"))
        record["rounded_fields"] = json.loads(record.pop("rounded_fields_json"))
        records.append(record)
    return records


def get_record_sources(db: sqlite3.Connection, record_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            """SELECT s.* FROM sources s JOIN record_sources rs ON rs.source_id=s.id
               WHERE rs.record_id=? ORDER BY s.id""",
            (record_id,),
        )
    ]


def get_history(db: sqlite3.Connection, record_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT field_name,old_value,new_value,edited_at FROM edit_history WHERE record_id=? ORDER BY id",
            (record_id,),
        )
    ]


def export_rows(db: sqlite3.Connection) -> list[dict[str, Any]]:
    result = []
    for record in list_records(db):
        row = {
            "Account": record["handle"],
            "Content ID": record["content_id"],
            "Title": record["title"],
            "Post URL": record["post_url"],
            "Publication date": record["publication_date"],
            "Content type": record["content_type"],
            "Snapshot ID": record["snapshot_label"],
            "Capture date/time": record["captured_at"],
            "Import date/time": record["import_datetime"],
            "Metric basis": record["metric_basis"],
            "Reporting period start": record["period_start"],
            "Reporting period end": record["period_end"],
            "Sources": record["source_filenames"],
            "Notes": record["notes"],
            "Review status": record["status"],
            "Rounded fields": ", ".join(record["rounded_fields"]),
            "Missing fields": ", ".join(
                field for field in METRIC_FIELDS if record["metrics"].get(field) is None
            ),
        }
        for field in METRIC_FIELDS:
            row[field] = record["metrics"].get(field)
            row[f"{field}_displayed"] = record["raw_values"].get(field)
        row.update(calculate_metrics(record["metrics"]))
        result.append(row)
    return result


def compare_snapshots(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare successive records only when their metric basis is identical."""
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((record["account_id"], record["content_id"]), []).append(record)
    changes = []
    for (_, content_id), items in grouped.items():
        items.sort(key=lambda item: (item["captured_at"] or item["created_at"], item["id"]))
        for before, after in zip(items, items[1:]):
            basis = before["metric_basis"]
            if basis not in {"lifetime", "selected period"} or basis != after["metric_basis"]:
                continue
            if basis == "selected period" and (
                before["period_start"] != after["period_start"]
                or before["period_end"] != after["period_end"]
            ):
                continue
            delta = {
                field: after["metrics"].get(field) - before["metrics"].get(field)
                for field in METRIC_FIELDS
                if before["metrics"].get(field) is not None
                and after["metrics"].get(field) is not None
            }
            changes.append(
                {
                    "content_id": content_id,
                    "title": after["title"],
                    "from_snapshot": before["snapshot_label"],
                    "to_snapshot": after["snapshot_label"],
                    "deltas": delta,
                }
            )
    return changes


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None
