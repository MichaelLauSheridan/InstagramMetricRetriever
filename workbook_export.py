from __future__ import annotations

import io
import sqlite3
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from metrics_store import calculate_metrics


def build_workbook(
    records: list[dict[str, Any]], frame: pd.DataFrame, db: sqlite3.Connection
) -> bytes:
    buffer = io.BytesIO()
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary.append(["SFWC Instagram Metrics - Local Export"])
    summary.append(["Generated locally", pd.Timestamp.now().to_pydatetime().replace(tzinfo=None)])
    summary.append(["Post records", len(records)])
    summary.append(["Accounts", len({row["account_id"] for row in records})])
    summary.append(["Snapshots", len({row["snapshot_id"] for row in records})])
    summary.append(["Note", "No totals across repeated snapshots; viewers are not summed as unique people."])
    summary.append([])
    summary.append(["Records flagged for interaction discrepancy"])
    summary.append(
        ["Account", "Title", "Content ID", "Snapshot ID", "Reported interactions",
         "Itemized interactions", "Difference"]
    )
    for record in records:
        checks = calculate_metrics(record["metrics"])
        if checks["interaction_difference"] not in (None, 0):
            summary.append(
                [
                    record["handle"], record["title"], record["content_id"],
                    record["snapshot_label"], record["metrics"].get("reported_interactions"),
                    checks["itemized_interactions"], checks["interaction_difference"],
                ]
            )

    log = workbook.create_sheet("Content log")
    detail = workbook.create_sheet("Post details")
    header_fill = PatternFill("solid", fgColor="244C3A")
    header_font = Font(color="FFFFFF", bold=True)
    content_columns = [
        "Account", "Content ID", "Title", "Post URL", "Publication date",
        "Content type", "Snapshot ID", "Capture date/time", "Import date/time",
        "Metric basis", "Reporting period start", "Reporting period end",
        "Sources", "Notes", "Review status", "Rounded fields", "Missing fields",
        "views", "viewers", "accounts_reached",
        "reported_interactions", "likes", "comments", "saves", "shares",
    ]
    for sheet, data_frame in ((log, frame[content_columns]), (detail, frame)):
        sheet.append(list(data_frame.columns))
        for values in data_frame.itertuples(index=False, name=None):
            typed_values = []
            for column, value in zip(data_frame.columns, values):
                if pd.isna(value):
                    typed_values.append(None)
                elif column in {
                    "Publication date", "Capture date/time", "Import date/time",
                    "Reporting period start", "Reporting period end",
                }:
                    try:
                        parsed_date = pd.to_datetime(value, errors="raise").to_pydatetime()
                        typed_values.append(parsed_date.replace(tzinfo=None))
                    except (ValueError, TypeError, AttributeError):
                        typed_values.append(value)
                else:
                    typed_values.append(value)
            sheet.append(typed_values)
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for col_idx, column in enumerate(data_frame.columns, start=1):
            sheet.column_dimensions[get_column_letter(col_idx)].width = min(
                max(len(str(column)) + 2, 14), 34
            )
        for row_index in range(2, sheet.max_row + 1):
            for column_index, column in enumerate(data_frame.columns, start=1):
                cell = sheet.cell(row_index, column_index)
                if column.endswith("_pct"):
                    cell.number_format = '0.0"%"'
                elif "date" in column.casefold() or column in {
                    "Capture date/time", "Import date/time"
                }:
                    cell.number_format = "yyyy-mm-dd hh:mm"

    account_sheet = workbook.create_sheet("Account snapshots")
    account_sheet.append(["Account", "Snapshot ID", "Capture date/time", "Followers", "Other metrics"])
    for row in db.execute(
        """SELECT a.handle,s.label,s.captured_at,x.followers_total,x.metrics_json
           FROM account_snapshots x JOIN accounts a ON a.id=x.account_id
           JOIN snapshots s ON s.id=x.snapshot_id ORDER BY s.created_at"""
    ):
        account_sheet.append(list(row))
    for cell in account_sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
    account_sheet.freeze_panes = "A2"
    workbook.save(buffer)
    return buffer.getvalue()
