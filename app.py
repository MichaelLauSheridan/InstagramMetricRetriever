from __future__ import annotations

import csv
import io
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from metrics_store import (
    APP_HOME,
    INTEGER_FIELDS,
    METRIC_FIELDS,
    add_account,
    add_pending_draft,
    add_snapshot,
    calculate_metrics,
    compare_snapshots,
    connect,
    delete_pending_draft,
    export_rows,
    extract_suggestions,
    get_history,
    get_record_sources,
    list_pending_drafts,
    list_records,
    parse_metric_value,
    save_post_record,
    store_source,
    tesseract_available,
)
from workbook_export import build_workbook

st.set_page_config(page_title="SFWC Instagram Metrics", page_icon="📊", layout="wide")
st.title("SFWC Instagram Metrics")
st.caption("Private, local collection and review. Screenshots, OCR, and the database remain on this computer.")

os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
db = connect()

METRIC_LABELS = {
    "views": "Views",
    "viewers": "Viewers (not reach)",
    "accounts_reached": "Accounts reached (not viewers)",
    "reported_interactions": "Interactions reported",
    "likes": "Likes",
    "comments": "Comments",
    "saves": "Saves",
    "shares": "Shares",
    "accounts_engaged": "Accounts engaged",
    "follower_views_pct": "Follower views (%)",
    "non_follower_views_pct": "Non-follower views (%)",
    "views_home": "Views from Home",
    "views_profile": "Views from Profile",
    "views_other": "Views from Other",
    "profile_activity": "Profile activity",
    "profile_visits": "Profile visits",
    "external_link_taps": "External link taps",
    "business_address_taps": "Business address taps",
    "follows": "Follows",
}
HEADER_ALIASES = {
    "post id": "content_id",
    "content id": "content_id",
    "media id": "content_id",
    "instagram post id": "content_id",
    "post url": "post_url",
    "url": "post_url",
    "post title": "title",
    "title": "title",
    "post name": "title",
    "publication date": "publication_date",
    "published": "publication_date",
    "date posted": "publication_date",
    "content type": "content_type",
    "snapshot id": "snapshot_label",
    "snapshot": "snapshot_label",
    "metric basis": "metric_basis",
    "reporting period start": "period_start",
    "period start": "period_start",
    "reporting period end": "period_end",
    "period end": "period_end",
    "notes": "notes",
    "paid organic": "paid_organic",
    "paid/organic": "paid_organic",
}
for metric_field, label in METRIC_LABELS.items():
    HEADER_ALIASES[label.casefold()] = metric_field
    HEADER_ALIASES[metric_field.replace("_", " ")] = metric_field
HEADER_ALIASES.update(
    {
        "interactions": "reported_interactions",
        "reported interactions": "reported_interactions",
        "accounts reached": "accounts_reached",
        "reach": "accounts_reached",
        "from home": "views_home",
        "from profile": "views_profile",
        "from other": "views_other",
        "follower views": "follower_views_pct",
        "non follower views": "non_follower_views_pct",
    }
)


def accounts() -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute("SELECT id,handle FROM accounts ORDER BY handle")]


def snapshots(account_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            """SELECT id,label,captured_at,metric_basis,period_start,period_end
               FROM snapshots WHERE account_id=? ORDER BY created_at DESC""",
            (account_id,),
        )
    ]


def draw_metric_inputs(key: str, initial_raw: dict[str, str] | None = None) -> tuple[dict[str, int | float | None], dict[str, str], set[str]]:
    initial_raw = initial_raw or {}
    values: dict[str, int | float | None] = {}
    raw: dict[str, str] = {}
    rounded: set[str] = set()
    for start in range(0, len(METRIC_FIELDS), 3):
        cols = st.columns(3)
        for col, field in zip(cols, METRIC_FIELDS[start : start + 3]):
            with col:
                entered = st.text_input(
                    METRIC_LABELS[field],
                    value=initial_raw.get(field, ""),
                    key=f"{key}_{field}",
                    help=(
                        "Enter percentage points, for example 1.2%. "
                        "Leave blank or enter -- when unavailable."
                        if field in {"follower_views_pct", "non_follower_views_pct"}
                        else "Leave blank or enter -- when unavailable. Keep Instagram's displayed value here."
                    ),
                )
            raw[field] = entered.strip()
            try:
                parsed = parse_metric_value(
                    entered,
                    percentage=field in {"follower_views_pct", "non_follower_views_pct"},
                )
                values[field] = parsed.value
                if parsed.rounded:
                    rounded.add(field)
            except ValueError as exc:
                st.error(f"{METRIC_LABELS[field]}: {exc}")
                values[field] = None
    return values, raw, rounded


def _raw_csv_value(row: dict[str, Any], aliases: dict[str, str], field: str) -> str:
    for header, mapped in aliases.items():
        if mapped == field:
            value = row.get(header)
            return "" if value is None else str(value).strip()
    return ""


def _normalize_csv_row(raw_row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    normalized = {str(key).strip().casefold(): value for key, value in raw_row.items()}
    aliases = {}
    for header in normalized:
        clean = " ".join(header.replace("_", " ").replace("-", " ").split())
        aliases[clean] = HEADER_ALIASES.get(clean)
    fields: dict[str, Any] = {}
    metrics: dict[str, str] = {}
    for header, value in normalized.items():
        clean = " ".join(header.replace("_", " ").replace("-", " ").split())
        field = HEADER_ALIASES.get(clean)
        if field in METRIC_FIELDS:
            metrics[field] = "" if value is None else str(value).strip()
        elif field:
            fields[field] = "" if value is None else str(value).strip()
    basis = (fields.get("metric_basis") or "unknown").strip().casefold()
    fields["metric_basis"] = basis if basis in {"unknown", "lifetime", "selected period"} else "unknown"
    content_type = (fields.get("content_type") or "other").strip().casefold()
    fields["content_type"] = (
        content_type if content_type in {"post", "carousel", "reel", "story", "other"} else "other"
    )
    fields["title"] = fields.get("title") or ""
    fields["post_url"] = fields.get("post_url") or ""
    fields["publication_date"] = fields.get("publication_date") or ""
    fields["period_start"] = fields.get("period_start") or ""
    fields["period_end"] = fields.get("period_end") or ""
    fields["notes"] = fields.get("notes") or ""
    paid_organic = (fields.get("paid_organic") or "").strip().casefold()
    fields["paid_organic"] = paid_organic if paid_organic in {"paid", "organic"} else ""
    return fields, metrics


def try_ocr(data: bytes, filename: str) -> tuple[str, str | None]:
    if not tesseract_available():
        return "", "Tesseract was not found. The original image is imported; enter metrics manually or install Tesseract."
    try:
        from PIL import Image
        import pytesseract

        text = pytesseract.image_to_string(Image.open(io.BytesIO(data)))
        return text, None
    except Exception as exc:
        return "", f"Local OCR failed for {filename}: {exc}. You can still enter the values manually."


with st.sidebar:
    st.subheader("Account and snapshot")
    current_accounts = accounts()
    with st.expander("Create account"):
        with st.form("create_account"):
            new_handle = st.text_input("Instagram handle", placeholder="@account")
            add_account_button = st.form_submit_button("Add account")
        if add_account_button:
            try:
                add_account(db, new_handle)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    account_id = None
    snapshot_id = None
    snapshot_list: list[dict[str, Any]] = []
    if current_accounts:
        selected_account = st.selectbox("Account", current_accounts, format_func=lambda a: f"@{a['handle']}")
        account_id = selected_account["id"]
        snapshot_list = snapshots(account_id)
        with st.expander("Create snapshot"):
            with st.form("create_snapshot"):
                snapshot_label = st.text_input("Snapshot ID", placeholder="e.g. 2026-10-15")
                captured_at = st.text_input("Capture date/time (optional)", placeholder="YYYY-MM-DD HH:MM")
                basis = st.selectbox("Metric basis", ["unknown", "lifetime", "selected period"])
                period_start = st.text_input("Reporting period start (optional)", placeholder="YYYY-MM-DD")
                period_end = st.text_input("Reporting period end (optional)", placeholder="YYYY-MM-DD")
                add_snapshot_button = st.form_submit_button("Add snapshot")
            if add_snapshot_button:
                try:
                    add_snapshot(
                        db, account_id, snapshot_label, captured_at, basis,
                        period_start or None, period_end or None,
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        if snapshot_list:
            selected_snapshot = st.selectbox(
                "Active snapshot",
                snapshot_list,
                format_func=lambda item: f"{item['label']} ({item['metric_basis']})",
            )
            snapshot_id = selected_snapshot["id"]
    else:
        st.info("Create an account to begin.")

tabs = st.tabs(["Import", "Review", "Records", "Summary", "Export"])

with tabs[0]:
    st.header("Import or enter metrics")
    st.caption("One screenshot batch represents one post in one snapshot. Attach all related screenshots together.")
    if not account_id or not snapshot_id:
        st.info("Create/select an account and snapshot in the sidebar.")
    else:
        st.subheader("Screenshots or manual entry")
        with st.form("stage_screenshot_batch", clear_on_submit=False):
            left, right = st.columns(2)
            with left:
                content_id = st.text_input("Stable post/content ID *")
                post_url = st.text_input("Post URL")
                title = st.text_input("Short title")
                publication_date = st.text_input("Publication date (optional)", placeholder="YYYY-MM-DD")
                content_type = st.selectbox("Content type", ["post", "carousel", "reel", "story", "other"])
            with right:
                basis_options = ["unknown", "lifetime", "selected period"]
                metric_basis = st.selectbox(
                    "Metric basis",
                    basis_options,
                    index=basis_options.index(selected_snapshot["metric_basis"]),
                )
                reporting_start = st.text_input(
                    "Reporting period start (optional)",
                    value=selected_snapshot["period_start"] or "",
                    placeholder="YYYY-MM-DD",
                )
                reporting_end = st.text_input(
                    "Reporting period end (optional)",
                    value=selected_snapshot["period_end"] or "",
                    placeholder="YYYY-MM-DD",
                )
                paid_organic = st.selectbox("Paid/organic status", ["unknown", "paid", "organic"])
                notes = st.text_area("Source notes")
            uploads = st.file_uploader(
                "Screenshots for this post (multiple files are grouped together)",
                type=["png", "jpg", "jpeg", "webp", "bmp"],
                accept_multiple_files=True,
                key="screenshot_uploader",
            )
            st.markdown("#### Manual values (optional)")
            manual_values: dict[str, str] = {}
            for start in range(0, len(METRIC_FIELDS), 3):
                cols = st.columns(3)
                for col, field in zip(cols, METRIC_FIELDS[start : start + 3]):
                    with col:
                        manual_values[field] = st.text_input(
                            METRIC_LABELS[field], key=f"manual_{field}",
                            help="Blank means unavailable; 0 is a genuine zero.",
                        )
            stage = st.form_submit_button("Send to review")
        if stage:
            if not content_id.strip():
                st.error("A stable post/content ID is required.")
            else:
                source_ids: list[int] = []
                raw_texts: list[str] = []
                duplicate_names: list[str] = []
                warnings: list[str] = []
                for upload in uploads or []:
                    data = upload.getvalue()
                    source_id, duplicate = store_source(db, upload.name, data, "image")
                    if duplicate:
                        duplicate_names.append(upload.name)
                    source_ids.append(source_id)
                    if duplicate:
                        saved_source = db.execute(
                            "SELECT raw_text FROM sources WHERE id=?", (source_id,)
                        ).fetchone()
                        text = saved_source["raw_text"] if saved_source else ""
                        warning = None
                    else:
                        text, warning = try_ocr(data, upload.name)
                    if text:
                        if not duplicate:
                            db.execute("UPDATE sources SET raw_text=? WHERE id=?", (text, source_id))
                            db.commit()
                        raw_texts.append(f"[{upload.name}]\n{text}")
                    if warning:
                        warnings.append(warning)
                if duplicate_names:
                    st.warning("Already imported (same file contents): " + ", ".join(duplicate_names))
                ocr_text = "\n\n".join(raw_texts)
                raw_values = extract_suggestions(ocr_text)
                for field, value in manual_values.items():
                    if value.strip():
                        raw_values[field] = value.strip()
                fields = {
                    "post_url": post_url or None,
                    "title": title or None,
                    "publication_date": publication_date or None,
                    "content_type": content_type,
                    "metric_basis": metric_basis,
                    "period_start": reporting_start or None,
                    "period_end": reporting_end or None,
                    "notes": notes or None,
                    "paid_organic": None if paid_organic == "unknown" else paid_organic,
                }
                add_pending_draft(
                    db, account_id=account_id, snapshot_id=snapshot_id,
                    content_id=content_id, fields=fields, raw_values=raw_values,
                    source_ids=source_ids, raw_text=ocr_text,
                )
                st.success("Staged for review. These suggested values are not confirmed until saved in Review.")
                for warning in warnings:
                    st.warning(warning)

        st.divider()
        st.subheader("Import a CSV or workbook")
        csv_upload = st.file_uploader("Select a CSV or .xlsx file", type=["csv", "xlsx"], key="csv_uploader")
        if csv_upload and st.button("Import file to review", key="import_csv_button"):
            data = csv_upload.getvalue()
            extension = Path(csv_upload.name).suffix.casefold()
            source_kind = "workbook" if extension == ".xlsx" else "csv"
            source_id, duplicate = store_source(db, csv_upload.name, data, source_kind)
            if duplicate:
                st.warning(f"{csv_upload.name} was already imported (matching file contents); no rows staged.")
            else:
                try:
                    if extension == ".xlsx":
                        imported_frame = pd.read_excel(io.BytesIO(data), sheet_name=0, dtype=object)
                        imported_frame = imported_frame.where(pd.notna(imported_frame), None)
                        reader_rows = imported_frame.to_dict(orient="records")
                        source_raw_text = "\n".join(
                            json.dumps(row, ensure_ascii=False, default=str) for row in reader_rows
                        )
                    else:
                        text = data.decode("utf-8-sig")
                        reader_rows = list(csv.DictReader(io.StringIO(text)))
                        source_raw_text = text
                    db.execute(
                        "UPDATE sources SET raw_text=? WHERE id=?",
                        (source_raw_text, source_id),
                    )
                    db.commit()
                    staged = 0
                    skipped = []
                    snapshot_mismatches = []
                    for row_number, raw_row in enumerate(reader_rows, start=2):
                        fields, raw_metrics = _normalize_csv_row(raw_row)
                        csv_snapshot = fields.pop("snapshot_label", "")
                        if csv_snapshot and csv_snapshot != selected_snapshot["label"]:
                            snapshot_mismatches.append((row_number, csv_snapshot))
                            continue
                        row_id = next(
                            (str(value).strip() for key, value in raw_row.items()
                             if HEADER_ALIASES.get(" ".join(key.strip().casefold().replace("_", " ").split())) == "content_id"),
                            "",
                        )
                        if not row_id:
                            skipped.append(row_number)
                            continue
                        if fields["metric_basis"] == "unknown":
                            fields["metric_basis"] = selected_snapshot["metric_basis"]
                        fields["period_start"] = (
                            fields["period_start"] or selected_snapshot["period_start"] or ""
                        )
                        fields["period_end"] = (
                            fields["period_end"] or selected_snapshot["period_end"] or ""
                        )
                        metrics_text = {
                            field: raw_metrics.get(field, "")
                            for field in METRIC_FIELDS
                        }
                        add_pending_draft(
                            db, account_id=account_id, snapshot_id=snapshot_id,
                            content_id=row_id, fields=fields,
                            raw_values={key: value for key, value in metrics_text.items() if value},
                            source_ids=[source_id],
                            raw_text=f"CSV row {row_number}: {json.dumps(raw_row, ensure_ascii=False)}",
                        )
                        staged += 1
                    st.success(f"Staged {staged} CSV row(s) for review.")
                    if skipped:
                        st.warning(f"Skipped rows without a stable post/content ID: {skipped}")
                    if snapshot_mismatches:
                        st.warning(
                            "Skipped rows whose Snapshot ID differs from the active snapshot "
                            f"({selected_snapshot['label']}): {snapshot_mismatches}"
                        )
                    if extension == ".xlsx":
                        st.info("The first worksheet was imported. Review each staged row before saving.")
                except (UnicodeDecodeError, csv.Error, ValueError, ImportError) as exc:
                    st.error(f"Could not read the import file: {exc}")

        st.divider()
        st.subheader("Account-level snapshot")
        with st.form("account_snapshot"):
            followers_total = st.text_input("Follower total")
            account_metrics_json = st.text_area(
                "Other account-level metrics (JSON, optional)",
                placeholder='{"profile_visits": 12}',
            )
            account_raw_json = st.text_area(
                "Displayed account metric values (JSON, optional)",
                placeholder='{"profile_visits": "12"}',
            )
            save_account_snapshot = st.form_submit_button("Save account snapshot")
        if save_account_snapshot:
            try:
                parsed_followers = parse_metric_value(followers_total).value
                parsed_other = json.loads(account_metrics_json or "{}")
                raw_other = json.loads(account_raw_json or "{}")
                db.execute(
                    """INSERT INTO account_snapshots
                       (account_id,snapshot_id,followers_total,metrics_json,raw_values_json,status)
                       VALUES(?,?,?,?,?,'reviewed')
                       ON CONFLICT(account_id,snapshot_id) DO UPDATE SET
                       followers_total=excluded.followers_total,metrics_json=excluded.metrics_json,
                       raw_values_json=excluded.raw_values_json""",
                    (account_id, snapshot_id, parsed_followers,
                     json.dumps(parsed_other), json.dumps(raw_other)),
                )
                db.commit()
                st.success("Account snapshot saved.")
            except (ValueError, json.JSONDecodeError) as exc:
                st.error(f"Could not save account snapshot: {exc}")

with tabs[1]:
    st.header("Review imported values")
    drafts = list_pending_drafts(db)
    if not drafts:
        st.info("No pending imports. New OCR and CSV values appear here before they become records.")
    for draft in drafts:
        label = f"{draft['handle']} · {draft['content_id']} · {draft['snapshot_label']}"
        with st.expander(label, expanded=len(drafts) == 1):
            st.caption("Review every suggested value. Blank means unavailable; it does not mean zero.")
            sources = [dict(row) for row in db.execute(
                "SELECT id,filename,path,kind,raw_text FROM sources WHERE id IN (%s)" %
                (",".join("?" * len(draft["source_ids"])) or "NULL"),
                draft["source_ids"],
            )] if draft["source_ids"] else []
            if draft["raw_text"]:
                with st.expander("Preserved OCR / imported raw text"):
                    st.text(draft["raw_text"])
            content_id_edit = st.text_input(
                "Stable post/content ID", draft["content_id"], key=f"review_id_{draft['id']}"
            )
            f = draft["fields"]
            if draft["raw_text"]:
                missing_from_extraction = [
                    METRIC_LABELS[field]
                    for field in METRIC_FIELDS
                    if not draft["raw_values"].get(field)
                ]
                if missing_from_extraction:
                    st.warning(
                        "No value was extracted for these fields; they are blank for manual review: "
                        + ", ".join(missing_from_extraction)
                    )
            top = st.columns(3)
            with top[0]:
                title_edit = st.text_input("Title", f.get("title") or "", key=f"review_title_{draft['id']}")
                url_edit = st.text_input("Post URL", f.get("post_url") or "", key=f"review_url_{draft['id']}")
                pub_edit = st.text_input("Publication date", f.get("publication_date") or "", key=f"review_pub_{draft['id']}")
            with top[1]:
                type_edit = st.selectbox(
                    "Content type", ["post", "carousel", "reel", "story", "other"],
                    index=["post", "carousel", "reel", "story", "other"].index(f.get("content_type", "other")),
                    key=f"review_type_{draft['id']}",
                )
                basis_options = ["unknown", "lifetime", "selected period"]
                basis_edit = st.selectbox(
                    "Metric basis", basis_options,
                    index=basis_options.index(f.get("metric_basis", "unknown")),
                    key=f"review_basis_{draft['id']}",
                )
                paid_edit = st.selectbox(
                    "Paid/organic", ["unknown", "paid", "organic"],
                    index=["unknown", "paid", "organic"].index(f.get("paid_organic") or "unknown"),
                    key=f"review_paid_{draft['id']}",
                )
            with top[2]:
                start_edit = st.text_input("Period start", f.get("period_start") or "", key=f"review_start_{draft['id']}")
                end_edit = st.text_input("Period end", f.get("period_end") or "", key=f"review_end_{draft['id']}")
                notes_edit = st.text_area("Notes", f.get("notes") or "", key=f"review_notes_{draft['id']}")
            images = [
                source for source in sources
                if source["kind"] == "image" and Path(source["path"]).exists()
            ]
            if images:
                image_col, metric_col = st.columns([1, 1])
                with image_col:
                    for source in images:
                        st.image(source["path"], caption=source["filename"], use_container_width=True)
                    st.write("**Source files**")
                    for source in sources:
                        st.write(f"- {source['filename']} ({source['kind']})")
                with metric_col:
                    st.markdown("#### Editable metric review")
                    metric_values, raw_values, rounded_fields = draw_metric_inputs(
                        f"review_{draft['id']}", draft["raw_values"]
                    )
            else:
                if sources:
                    st.write("**Source files**")
                    for source in sources:
                        st.write(f"- {source['filename']} ({source['kind']})")
                else:
                    st.write("No source files attached; this is a manual entry.")
                metric_values, raw_values, rounded_fields = draw_metric_inputs(
                    f"review_{draft['id']}", draft["raw_values"]
                )
            calculations = calculate_metrics(metric_values)
            if rounded_fields:
                st.info(
                    "Approximate rounded values: "
                    + ", ".join(METRIC_LABELS[field] for field in sorted(rounded_fields))
                    + ". Confirm them against the source before saving."
                )
            if calculations["interaction_difference"] not in (None, 0):
                st.warning(
                    "Review discrepancy: reported interactions minus itemized likes + comments + saves + "
                    f"shares is {calculations['interaction_difference']}."
                )
            elif calculations["interaction_difference"] == 0:
                st.info("Reported interactions match the itemized interaction total.")
            if calculations["view_sources_total"] is not None and metric_values.get("views") is not None:
                if calculations["view_sources_total"] != metric_values["views"]:
                    st.warning(
                        f"Review discrepancy: view sources total {calculations['view_sources_total']}, "
                        f"while views are {metric_values['views']}."
                    )
            actions = st.columns(2)
            with actions[0]:
                save_review = st.button("Confirm and save record", key=f"save_draft_{draft['id']}")
            with actions[1]:
                discard_review = st.button("Discard pending draft", key=f"discard_draft_{draft['id']}")
            if save_review:
                try:
                    save_post_record(
                        db,
                        account_id=draft["account_id"],
                        snapshot_id=draft["snapshot_id"],
                        content_id=content_id_edit,
                        fields={
                            "title": title_edit or None,
                            "post_url": url_edit or None,
                            "publication_date": pub_edit or None,
                            "content_type": type_edit,
                            "metric_basis": basis_edit,
                            "period_start": start_edit or None,
                            "period_end": end_edit or None,
                            "notes": notes_edit or None,
                            "paid_organic": None if paid_edit == "unknown" else paid_edit,
                        },
                        metrics=metric_values,
                        raw_values=raw_values,
                        rounded_fields=rounded_fields,
                        source_ids=draft["source_ids"],
                        imported_at=draft["created_at"],
                    )
                    delete_pending_draft(db, draft["id"])
                    st.success("Reviewed record saved.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            if discard_review:
                delete_pending_draft(db, draft["id"])
                st.rerun()

with tabs[2]:
    st.header("Records")
    records = list_records(db)
    if records:
        filter_options = ["All accounts"] + sorted({record["handle"] for record in records})
        account_filter = st.selectbox("Filter account", filter_options)
        filtered = records if account_filter == "All accounts" else [
            record for record in records if record["handle"] == account_filter
        ]
        query = st.text_input("Search title or content ID")
        snapshot_filter = st.selectbox(
            "Filter snapshot",
            ["All snapshots"] + sorted({record["snapshot_label"] for record in filtered}),
        )
        if query:
            filtered = [
                record for record in filtered
                if query.casefold() in (record["title"] or "").casefold()
                or query.casefold() in record["content_id"].casefold()
            ]
        if snapshot_filter != "All snapshots":
            filtered = [
                record for record in filtered if record["snapshot_label"] == snapshot_filter
            ]
        if not filtered:
            st.info("No records match those filters.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Account": r["handle"],
                            "Post": r["title"] or r["content_id"],
                            "Snapshot": r["snapshot_label"],
                            "Views": r["metrics"].get("views"),
                            "Viewers": r["metrics"].get("viewers"),
                            "Accounts reached": r["metrics"].get("accounts_reached"),
                            "Interactions": r["metrics"].get("reported_interactions"),
                            "Likes": r["metrics"].get("likes"),
                            "Comments": r["metrics"].get("comments"),
                            "Saves": r["metrics"].get("saves"),
                            "Shares": r["metrics"].get("shares"),
                            "Status": r["status"],
                        }
                        for r in filtered
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            selected_record = st.selectbox(
                "Inspect or correct a record",
                filtered,
                format_func=lambda r: f"{r['handle']} · {r['title'] or r['content_id']} · {r['snapshot_label']}",
            )
            with st.expander("Record details and edit history"):
                st.json(
                    {
                        "Post": {key: selected_record[key] for key in (
                            "content_id", "title", "post_url", "publication_date", "content_type",
                            "snapshot_label", "captured_at", "metric_basis", "period_start",
                            "period_end", "created_at", "import_datetime", "source_filenames",
                            "notes", "status",
                        )},
                        "Metrics": {
                            field: (
                                f"{value:g}%"
                                if field in {"follower_views_pct", "non_follower_views_pct"}
                                and value is not None
                                else value if value is not None else "Unavailable"
                            )
                            for field, value in selected_record["metrics"].items()
                        },
                        "Displayed source values": selected_record["raw_values"],
                        "Rounded fields": selected_record["rounded_fields"],
                        "Calculated checks": calculate_metrics(selected_record["metrics"]),
                        "Edit history": get_history(db, selected_record["id"]),
                    }
                )
                for source in get_record_sources(db, selected_record["id"]):
                    if source["kind"] == "image" and Path(source["path"]).exists():
                        st.image(source["path"], caption=source["filename"], width=360)
                    if source["raw_text"]:
                        st.caption(f"{source['filename']} imported text")
                        st.text(source["raw_text"])
                with st.form(f"edit_record_{selected_record['id']}"):
                    st.write("Edit values (changes are added to the audit history).")
                    metrics, raw, rounded = draw_metric_inputs(
                        f"edit_{selected_record['id']}",
                        {
                            field: (
                                "" if selected_record["metrics"].get(field) is None
                                else str(selected_record["metrics"][field])
                            )
                            for field in METRIC_FIELDS
                        },
                    )
                    submitted = st.form_submit_button("Save corrections")
                if submitted:
                    fields = {
                        key: selected_record[key]
                        for key in (
                            "post_url", "title", "publication_date", "content_type", "metric_basis",
                            "period_start", "period_end", "notes", "paid_organic",
                        )
                    }
                    try:
                        save_post_record(
                            db, account_id=selected_record["account_id"],
                            snapshot_id=selected_record["snapshot_id"],
                            content_id=selected_record["content_id"], fields=fields,
                            metrics=metrics, raw_values=raw, rounded_fields=rounded,
                            source_ids=[source["id"] for source in get_record_sources(db, selected_record["id"])],
                            record_id=selected_record["id"],
                        )
                        st.success("Corrections saved.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
    else:
        st.info("No reviewed post records yet.")

with tabs[3]:
    st.header("Summary")
    records = list_records(db)
    if not records:
        st.info("Review and save records to see a summary.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Reviewed post records", len(records))
        c2.metric("Accounts", len({record["account_id"] for record in records}))
        c3.metric("Snapshots", len({record["snapshot_id"] for record in records}))
        st.caption(
            "Totals below describe saved record rows only. Repeated snapshots are not summed, and viewers are not unique people across posts."
        )
        for record in records:
            checks = calculate_metrics(record["metrics"])
            if checks["interaction_difference"] not in (None, 0):
                st.warning(
                    f"{record['handle']} · {record['title'] or record['content_id']} · {record['snapshot_label']}: "
                    f"interaction difference {checks['interaction_difference']}."
                )
            if (
                checks["view_sources_total"] is not None
                and record["metrics"].get("views") is not None
                and checks["view_sources_total"] != record["metrics"]["views"]
            ):
                st.warning(
                    f"{record['handle']} · {record['title'] or record['content_id']}: "
                    f"source views total {checks['view_sources_total']} differs from views {record['metrics']['views']}."
                )
        changes = compare_snapshots(records)
        if changes:
            st.subheader("Comparable snapshot changes")
            st.caption("Deltas are shown only for successive snapshots with the same metric basis.")
            for change in changes:
                st.write(
                    f"**{change['title'] or change['content_id']}** — "
                    f"{change['from_snapshot']} → {change['to_snapshot']}: {change['deltas']}"
                )
        st.subheader("Account snapshots")
        account_rows = db.execute(
            """SELECT a.handle,s.label,s.captured_at,x.followers_total,x.metrics_json
               FROM account_snapshots x JOIN accounts a ON a.id=x.account_id
               JOIN snapshots s ON s.id=x.snapshot_id ORDER BY s.created_at DESC"""
        ).fetchall()
        if account_rows:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Account": row["handle"],
                            "Snapshot": row["label"],
                            "Capture date/time": row["captured_at"],
                            "Followers": row["followers_total"],
                            "Other metrics": row["metrics_json"],
                        }
                        for row in account_rows
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No account-level snapshots saved.")

with tabs[4]:
    st.header("Export")
    records = list_records(db)
    rows = export_rows(db)
    if not rows:
        st.info("No saved records to export.")
    else:
        frame = pd.DataFrame(rows)
        st.download_button(
            "Download content log CSV",
            frame.to_csv(index=False).encode("utf-8-sig"),
            file_name="sfwc_instagram_content_log.csv",
            mime="text/csv",
        )
        workbook_bytes = build_workbook(records, frame, db)
        st.download_button(
            "Download Excel workbook",
            workbook_bytes,
            file_name="sfwc_instagram_metrics.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.caption("Source display values and rounded-value markers are included alongside parsed values.")
