from io import BytesIO
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from metrics_store import (
    METRIC_FIELDS,
    add_account,
    add_snapshot,
    calculate_metrics,
    compare_snapshots,
    connect,
    extract_suggestions,
    get_history,
    list_records,
    parse_metric_value,
    export_rows,
    save_post_record,
    store_source,
)
from workbook_export import build_workbook


EXAMPLE_ACCEPTANCE_FIXTURE_NOT_PRODUCTION = {
    "title": "Consumed by Aja Barber book spotlight",
    "values": {
        "views": 5029,
        "viewers": 3143,
        "reported_interactions": 112,
        "likes": 95,
        "comments": 3,
        "saves": 6,
        "shares": 4,
        "accounts_engaged": 99,
        "follower_views_pct": 1.2,
        "non_follower_views_pct": 98.8,
        "views_home": 3851,
        "views_profile": 1087,
        "views_other": 91,
        "profile_activity": 3,
        "profile_visits": 3,
        "external_link_taps": 0,
        "business_address_taps": 0,
        "follows": 0,
    },
}


class MetricsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = connect(self.root / "test.sqlite3")
        self.account_id = add_account(self.db, "@sfwctest")
        self.snapshot_id = add_snapshot(
            self.db, self.account_id, "snapshot-1", "2026-10-01 10:00",
            "lifetime",
        )

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_missing_is_null_and_zero_is_preserved(self):
        self.assertIsNone(parse_metric_value("").value)
        self.assertIsNone(parse_metric_value("--").value)
        self.assertEqual(parse_metric_value("0").value, 0)
        self.assertIsNone(calculate_metrics({"likes": None})["itemized_interactions"])

    def test_exact_and_rounded_display_values(self):
        exact = parse_metric_value("5,029")
        self.assertEqual(exact.value, 5029)
        self.assertEqual(exact.raw, "5,029")
        self.assertFalse(exact.rounded)

        rounded = parse_metric_value("1.6K")
        self.assertEqual(rounded.value, 1600)
        self.assertEqual(rounded.raw, "1.6K")
        self.assertTrue(rounded.rounded)

        percentage = parse_metric_value("1.2%", percentage=True)
        self.assertEqual(percentage.value, 1.2)

    def test_ocr_suggestions_are_conservative_and_retain_displayed_text(self):
        suggestions = extract_suggestions(
            "Views 5,029\nViewers 2,100\nAccounts reached 2,050\n"
            "Likes 95\nFrom Home 3,851\nFollowers 1.2%"
        )
        self.assertEqual(suggestions["views"], "5,029")
        self.assertEqual(suggestions["viewers"], "2,100")
        self.assertEqual(suggestions["accounts_reached"], "2,050")
        self.assertEqual(suggestions["views_home"], "3,851")
        self.assertEqual(suggestions["follower_views_pct"], "1.2%")
        self.assertNotIn("publication_date", suggestions)

    def test_duplicate_file_import_warns_and_does_not_copy_again(self):
        payload = b"private sample image bytes"
        first_id, first_duplicate = store_source(
            self.db, "screen.png", payload, "image", storage_dir=self.root / "imports"
        )
        second_id, second_duplicate = store_source(
            self.db, "renamed.png", payload, "image", storage_dir=self.root / "imports"
        )
        self.assertFalse(first_duplicate)
        self.assertTrue(second_duplicate)
        self.assertEqual(first_id, second_id)
        self.assertEqual(len(list((self.root / "imports").iterdir())), 1)

    def test_same_post_can_have_later_snapshot_but_not_duplicate_record(self):
        fields = {"title": "Test post", "metric_basis": "lifetime"}
        metrics = {field: None for field in METRIC_FIELDS}
        metrics["views"] = 10
        record_id = save_post_record(
            self.db, account_id=self.account_id, snapshot_id=self.snapshot_id,
            content_id="post-abc", fields=fields, metrics=metrics,
            raw_values={"views": "10"}, rounded_fields=[],
        )
        with self.assertRaisesRegex(ValueError, "already exists"):
            save_post_record(
                self.db, account_id=self.account_id, snapshot_id=self.snapshot_id,
                content_id="post-abc", fields=fields, metrics=metrics,
                raw_values={"views": "10"}, rounded_fields=[],
            )

        later = add_snapshot(
            self.db, self.account_id, "snapshot-2", "2026-10-15 10:00",
            "lifetime",
        )
        metrics["views"] = 20
        second_id = save_post_record(
            self.db, account_id=self.account_id, snapshot_id=later,
            content_id="post-abc", fields=fields, metrics=metrics,
            raw_values={"views": "20"}, rounded_fields=[],
        )
        self.assertNotEqual(record_id, second_id)

    def test_discrepancy_and_view_source_checks(self):
        values = EXAMPLE_ACCEPTANCE_FIXTURE_NOT_PRODUCTION["values"]
        checks = calculate_metrics(values)
        self.assertEqual(checks["itemized_interactions"], 108)
        self.assertEqual(checks["interaction_difference"], 4)
        self.assertEqual(checks["view_sources_total"], 5029)

    def test_snapshot_changes_require_comparable_basis(self):
        base = {
            "account_id": self.account_id,
            "content_id": "same-post",
            "captured_at": "2026-10-01",
            "created_at": "2026-10-01",
            "id": 1,
            "metric_basis": "lifetime",
            "period_start": None,
            "period_end": None,
            "snapshot_label": "one",
            "title": "Same post",
            "metrics": {"views": 10},
        }
        later = dict(base, captured_at="2026-10-15", id=2, snapshot_label="two",
                     metrics={"views": 25})
        changes = compare_snapshots([base, later])
        self.assertEqual(changes[0]["deltas"]["views"], 15)
        unknown = dict(later, metric_basis="unknown")
        self.assertEqual(compare_snapshots([base, unknown]), [])

    def test_correction_keeps_original_display_value_and_adds_history(self):
        metrics = {field: None for field in METRIC_FIELDS}
        metrics["views"] = 1600
        record_id = save_post_record(
            self.db, account_id=self.account_id, snapshot_id=self.snapshot_id,
            content_id="rounded-post", fields={"metric_basis": "lifetime"},
            metrics=metrics, raw_values={"views": "1.6K"},
            rounded_fields={"views"},
        )
        metrics["views"] = 1500
        save_post_record(
            self.db, account_id=self.account_id, snapshot_id=self.snapshot_id,
            content_id="rounded-post", fields={"metric_basis": "lifetime"},
            metrics=metrics, raw_values={"views": "1500"},
            rounded_fields=set(), record_id=record_id,
        )
        record = next(record for record in list_records(self.db) if record["id"] == record_id)
        self.assertEqual(record["metrics"]["views"], 1500)
        self.assertEqual(record["raw_values"]["views"], "1.6K")
        self.assertEqual(record["rounded_fields"], ["views"])
        self.assertEqual(len(get_history(self.db, record_id)), 1)

    def test_workbook_keeps_dates_numbers_percentages_and_sheets_typed(self):
        metrics = {field: None for field in METRIC_FIELDS}
        metrics.update({"views": 5029, "follower_views_pct": 1.2, "accounts_reached": 1600})
        save_post_record(
            self.db, account_id=self.account_id, snapshot_id=self.snapshot_id,
            content_id="export-post",
            fields={
                "title": "Export test", "publication_date": "2026-09-20",
                "metric_basis": "lifetime", "period_start": "2026-09-01",
            },
            metrics=metrics,
            raw_values={
                "views": "5,029", "follower_views_pct": "1.2%", "accounts_reached": "1.6K",
            },
            rounded_fields={"accounts_reached"}, imported_at="2026-10-01T10:00:00-05:00",
        )
        records = list_records(self.db)
        frame = pd.DataFrame(export_rows(self.db))
        workbook = load_workbook(BytesIO(build_workbook(records, frame, self.db)), data_only=True)
        self.assertEqual(
            workbook.sheetnames,
            ["Summary", "Content log", "Post details", "Account snapshots"],
        )
        sheet = workbook["Post details"]
        columns = {cell.value: cell.column for cell in sheet[1]}
        self.assertEqual(sheet.cell(2, columns["views"]).value, 5029)
        self.assertIsInstance(sheet.cell(2, columns["views"]).value, int)
        self.assertIsNone(sheet.cell(2, columns["viewers"]).value)
        self.assertIn("viewers", sheet.cell(2, columns["Missing fields"]).value)
        self.assertIn("accounts_reached", sheet.cell(2, columns["Rounded fields"]).value)
        self.assertEqual(sheet.cell(2, columns["accounts_reached"]).value, 1600)
        self.assertEqual(sheet.cell(2, columns["follower_views_pct"]).value, 1.2)
        self.assertIn("%", sheet.cell(2, columns["follower_views_pct"]).number_format)
        self.assertEqual(sheet.cell(2, columns["Publication date"]).value.year, 2026)
        self.assertEqual(sheet.cell(2, columns["Import date/time"]).value.hour, 10)


if __name__ == "__main__":
    unittest.main()
