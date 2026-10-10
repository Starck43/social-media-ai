"""Standalone stdlib regression checks; no app imports, conftest or database.

Owner command: python tests/test_date_parsing_compatibility.py
Pytest discovery is supported, but the standalone command avoids shared fixtures.
"""

import importlib.util
import inspect
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_SOURCE = Path(__file__).resolve().parents[1] / "app" / "utils" / "date_parsing.py"
_SPEC = importlib.util.spec_from_file_location("_date_parsing_contract", _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Cannot load standalone date parser from {_SOURCE}")
parser = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(parser)


class DateParsingCompatibilityTests(unittest.TestCase):
    def test_legacy_name_is_canonical_alias(self):
        self.assertIs(parser.universal_date_parser, parser.parse_datetime)

    def test_public_signatures_preserve_keyword_names_and_default(self):
        for function in (
            parser.parse_datetime,
            parser.universal_date_parser,
            parser.to_unix_timestamp,
        ):
            with self.subTest(function=function.__name__):
                parameters = inspect.signature(function).parameters
                self.assertEqual(list(parameters), ["date_input", "target_timezone"])
                self.assertEqual(parameters["target_timezone"].default, "UTC+3")

    def test_falsey_inputs_including_epoch_zero_remain_absent(self):
        for value in (None, "", 0, 0.0, False, [], {}):
            with self.subTest(value=value):
                self.assertIsNone(parser.parse_datetime(value))
                self.assertIsNone(parser.to_unix_timestamp(value))

    def test_aware_datetime_identity_and_offset_are_preserved(self):
        value = datetime(2026, 10, 10, 12, 30, tzinfo=timezone(timedelta(hours=3)))
        self.assertIs(parser.parse_datetime(value), value)

    def test_naive_datetime_gets_utc_without_shifting_clock(self):
        value = datetime(2026, 10, 10, 12, 30, 1, 123456)
        self.assertEqual(
            parser.parse_datetime(value), value.replace(tzinfo=timezone.utc)
        )
        self.assertIsNone(value.tzinfo)

    def test_date_gets_midnight_utc(self):
        self.assertEqual(
            parser.parse_datetime(date(2026, 10, 10)),
            datetime(2026, 10, 10, tzinfo=timezone.utc),
        )

    def test_day_first_formats_get_midnight_utc(self):
        expected = datetime(2026, 10, 10, tzinfo=timezone.utc)
        for value in ("10-10-2026", "10.10.2026"):
            with self.subTest(value=value):
                self.assertEqual(parser.parse_datetime(value), expected)

    def test_iso_naive_z_and_explicit_offsets(self):
        cases = (
            ("2026-10-10", datetime(2026, 10, 10, tzinfo=timezone.utc)),
            (
                "2026-10-10T12:30:01.123456",
                datetime(2026, 10, 10, 12, 30, 1, 123456, tzinfo=timezone.utc),
            ),
            (
                "2026-10-10T12:30:00Z",
                datetime(2026, 10, 10, 12, 30, tzinfo=timezone.utc),
            ),
            (
                "2026-10-10T12:30:00+03:00",
                datetime(2026, 10, 10, 12, 30, tzinfo=timezone(timedelta(hours=3))),
            ),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(parser.parse_datetime(value), expected)

    def test_timezone_argument_remains_compatibility_only(self):
        expected = datetime(2026, 10, 10, tzinfo=timezone.utc)
        for target in ("UTC+3", "UTC", "Europe/Kirov", "invalid", "", None):
            for value in ("10-10-2026", "10.10.2026", "2026-10-10"):
                with self.subTest(target=target, value=value):
                    self.assertEqual(
                        parser.universal_date_parser(
                            date_input=value, target_timezone=target
                        ),
                        expected,
                    )

    def test_unix_input_fractional_negative_and_bool_compatibility(self):
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        for value in (1, True, 1.25, -1, -0.25):
            with self.subTest(value=value):
                self.assertEqual(
                    parser.parse_datetime(value), epoch + timedelta(seconds=value)
                )
                self.assertEqual(parser.to_unix_timestamp(value), int(value))

    def test_invalid_strings_do_not_gain_whitespace_or_format_normalization(self):
        for value in (
            "synthetic-invalid",
            "31-02-2026",
            "10/10/2026",
            " 10-10-2026 ",
            "2026-10-10 ",
        ):
            with self.subTest(value=value), patch.object(
                parser.logger, "warning"
            ) as warning:
                self.assertIsNone(parser.parse_datetime(value))
                warning.assert_called_once_with(
                    f"Unsupported date string format: {value}"
                )

    def test_unsupported_types_keep_type_warning(self):
        for value in (object(), [1], {"synthetic": 1}):
            with self.subTest(type=type(value)), patch.object(
                parser.logger, "warning"
            ) as warning:
                self.assertIsNone(parser.parse_datetime(value))
                warning.assert_called_once_with(
                    f"Unsupported date input type: {type(value)}"
                )

    def test_out_of_range_or_non_finite_timestamp_returns_none(self):
        for value in (10**30, float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=value), patch.object(
                parser.logger, "warning"
            ) as warning:
                self.assertIsNone(parser.parse_datetime(value))
                self.assertTrue(
                    warning.call_args.args[0].startswith("Date parsing failed for ")
                )

    def test_unix_conversion_uses_legacy_hook_and_forwards_timezone(self):
        value = datetime(1970, 1, 1, 0, 0, 1, 750000, tzinfo=timezone.utc)
        with patch.object(
            parser, "universal_date_parser", return_value=value
        ) as legacy:
            self.assertEqual(parser.to_unix_timestamp("synthetic", "UTC"), 1)
            legacy.assert_called_once_with("synthetic", "UTC")

    def test_unix_conversion_keeps_offset_and_truncates_negative_fraction(self):
        self.assertEqual(parser.to_unix_timestamp("1970-01-01T03:00:01+03:00"), 1)
        self.assertEqual(parser.to_unix_timestamp("1969-12-31T23:59:59.750000Z"), 0)

    def test_unix_conversion_catches_timestamp_failure(self):
        class BrokenTimestamp:
            def timestamp(self):
                raise ValueError("synthetic conversion failure")

        with patch.object(
            parser, "universal_date_parser", return_value=BrokenTimestamp()
        ):
            with patch.object(parser.logger, "warning") as warning:
                self.assertIsNone(parser.to_unix_timestamp("synthetic"))
                warning.assert_called_once_with(
                    "Timestamp conversion failed: synthetic conversion failure"
                )


if __name__ == "__main__":
    unittest.main()
