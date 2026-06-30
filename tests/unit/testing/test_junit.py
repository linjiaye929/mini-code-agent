from pathlib import Path

import pytest

from mini_code_agent.testing.errors import PytestReportError, PytestReportErrorCode
from mini_code_agent.testing.junit import parse_junit_report
from mini_code_agent.testing.models import (
    PytestCounts,
    PytestDiagnosticOutcome,
    PytestLimits,
)


def write_report(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_parser_computes_counts_and_diagnostics_from_test_cases(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.xml"
    write_report(
        report,
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="999" failures="999">
  <testsuite name="sample">
    <testcase classname="tests.test_ok" name="test_pass" file="tests/test_ok.py" line="2" />
    <testcase classname="tests.test_bad" name="test_fail" file="tests/test_bad.py" line="7">
      <failure message="assert 1 == 2">tests/test_bad.py:8: AssertionError</failure>
    </testcase>
    <testcase classname="tests.test_error" name="test_error">
      <error message="setup failed">RuntimeError: private detail</error>
    </testcase>
    <testcase classname="tests.test_skip" name="test_skip">
      <skipped message="not supported" />
    </testcase>
  </testsuite>
</testsuites>
""",
    )

    parsed = parse_junit_report(report, PytestLimits())

    assert parsed.counts == PytestCounts(
        total=4,
        passed=1,
        failed=1,
        errors=1,
        skipped=1,
    )
    assert [item.outcome for item in parsed.diagnostics] == [
        PytestDiagnosticOutcome.FAILURE,
        PytestDiagnosticOutcome.ERROR,
    ]
    failure = parsed.diagnostics[0]
    assert failure.test_name == "test_fail"
    assert failure.class_name == "tests.test_bad"
    assert failure.file == "tests/test_bad.py"
    assert failure.line == 7
    assert failure.message == "assert 1 == 2"
    assert failure.details == "tests/test_bad.py:8: AssertionError"
    assert parsed.diagnostics_truncated is False


def test_parser_accepts_single_testsuite_and_empty_report(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    write_report(report, '<testsuite name="empty" tests="0" />')

    parsed = parse_junit_report(report, PytestLimits())

    assert parsed.counts == PytestCounts.empty()
    assert parsed.diagnostics == ()


def test_parser_truncates_diagnostics_and_text_to_configured_limits(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.xml"
    write_report(
        report,
        """<testsuite>
  <testcase classname="very.long.classname" name="test_first">
    <failure message="abcdefghij">0123456789abcdefghij</failure>
  </testcase>
  <testcase name="test_second"><error message="second">details</error></testcase>
</testsuite>""",
    )
    limits = PytestLimits(
        max_diagnostics=1,
        max_message_chars=8,
        max_details_chars=12,
    )

    parsed = parse_junit_report(report, limits)

    assert parsed.counts.failed == 1
    assert parsed.counts.errors == 1
    assert len(parsed.diagnostics) == 1
    assert len(parsed.diagnostics[0].message) == 8
    assert len(parsed.diagnostics[0].details) == 12
    assert parsed.diagnostics_truncated is True


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"\xff\xfe\x00", PytestReportErrorCode.INVALID),
        (b"<testsuite>", PytestReportErrorCode.INVALID),
        (b"<unknown />", PytestReportErrorCode.INVALID),
        (b"<testsuite><testcase /></testsuite>", PytestReportErrorCode.INVALID),
        (
            b"<testsuite><testcase name='x'><failure/><error/></testcase></testsuite>",
            PytestReportErrorCode.INVALID,
        ),
        (
            b"<testsuite><testcase name='x' line='-1'/></testsuite>",
            PytestReportErrorCode.INVALID,
        ),
        (
            b"<!DOCTYPE testsuite><testsuite />",
            PytestReportErrorCode.UNSAFE,
        ),
        (
            b"<!ENTITY x 'value'><testsuite />",
            PytestReportErrorCode.UNSAFE,
        ),
    ],
)
def test_parser_rejects_invalid_or_unsafe_reports(
    tmp_path: Path,
    content: bytes,
    code: PytestReportErrorCode,
) -> None:
    report = tmp_path / "report.xml"
    report.write_bytes(content)

    with pytest.raises(PytestReportError) as captured:
        parse_junit_report(report, PytestLimits())

    assert captured.value.code is code
    assert str(report) not in captured.value.public_message


def test_parser_maps_missing_report_to_static_error(tmp_path: Path) -> None:
    report = tmp_path / "missing.xml"

    with pytest.raises(PytestReportError) as captured:
        parse_junit_report(report, PytestLimits())

    assert captured.value.code is PytestReportErrorCode.MISSING
    assert str(report) not in captured.value.public_message


@pytest.mark.parametrize(
    ("size", "expected_error"),
    [
        (31, None),
        (32, None),
        (33, PytestReportErrorCode.TOO_LARGE),
    ],
)
def test_parser_enforces_report_byte_boundary(
    tmp_path: Path,
    size: int,
    expected_error: PytestReportErrorCode | None,
) -> None:
    base = b"<testsuite />"
    report = tmp_path / "report.xml"
    report.write_bytes(base + b" " * (size - len(base)))
    limits = PytestLimits(max_report_bytes=32)

    if expected_error is None:
        assert parse_junit_report(report, limits).counts == PytestCounts.empty()
    else:
        with pytest.raises(PytestReportError) as captured:
            parse_junit_report(report, limits)
        assert captured.value.code is expected_error


def test_parser_rejects_case_count_above_limit(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    write_report(
        report,
        """<testsuite>
  <testcase name="one" />
  <testcase name="two" />
  <testcase name="three" />
</testsuite>""",
    )

    with pytest.raises(PytestReportError) as captured:
        parse_junit_report(report, PytestLimits(max_cases=2))

    assert captured.value.code is PytestReportErrorCode.TOO_LARGE


def test_parser_rejects_non_regular_report(tmp_path: Path) -> None:
    with pytest.raises(PytestReportError) as captured:
        parse_junit_report(tmp_path, PytestLimits())

    assert captured.value.code is PytestReportErrorCode.UNSAFE
