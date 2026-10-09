import pytest

from app.csv_parser import parse_hospitals
from app.schemas import HospitalRow


def parse(text: str | bytes, max_rows: int = 20):
    return parse_hospitals(text.encode() if isinstance(text, str) else text, max_rows)


def file_error(text: str | bytes, max_rows: int = 20) -> str:
    report = parse(text, max_rows)
    assert not report.valid
    assert report.rows == []
    [error] = report.errors
    assert error.row is None
    return error.message


def test_parses_valid_rows() -> None:
    report = parse("name,address,phone\nGeneral,1 Main St,555-1234\nMercy,2 Elm St,\n")

    assert report.valid
    assert report.total_rows == 2
    assert report.rows == [
        HospitalRow(row=1, name="General", address="1 Main St", phone="555-1234"),
        HospitalRow(row=2, name="Mercy", address="2 Elm St", phone=None),
    ]


def test_phone_column_is_optional() -> None:
    report = parse("name,address\nGeneral,1 Main St\n")

    assert report.valid
    assert report.rows[0].phone is None


def test_normalises_bom_case_whitespace_and_column_order() -> None:
    text = "﻿ Address , NAME ,Phone\n  1 Main St , General , 555 \n"

    report = parse(text.encode())

    assert report.rows == [HospitalRow(row=1, name="General", address="1 Main St", phone="555")]


def test_skips_blank_lines_without_consuming_row_numbers() -> None:
    report = parse("name,address\n\nA,1 St\n   \n,\nB,2 St\n\n")

    assert [(r.row, r.name) for r in report.rows] == [(1, "A"), (2, "B")]


def test_handles_quoted_commas() -> None:
    report = parse('name,address\n"Kids, North","1 Park Pl, Suite 2"\n')

    assert report.rows[0].name == "Kids, North"
    assert report.rows[0].address == "1 Park Pl, Suite 2"


def test_accepts_exactly_max_rows() -> None:
    rows = "".join(f"H{i},{i} St\n" for i in range(20))

    assert parse("name,address\n" + rows).total_rows == 20


def test_reports_every_invalid_row_and_keeps_valid_ones() -> None:
    report = parse("name,address,phone\nOK,1 St,\n,2 St,\nNoAddr,,\nX,4 St,555,extra\n,,\n")

    assert not report.valid
    assert [r.row for r in report.rows] == [1]
    assert [(e.row, e.message) for e in report.errors] == [
        (2, "missing value for: name"),
        (3, "missing value for: address"),
        (4, "expected at most 3 fields, got 4"),
    ]


def test_short_rows_are_padded_as_missing_values() -> None:
    report = parse("name,address,phone\nOnlyName\n")

    assert report.errors[0].message == "missing value for: address"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "CSV is empty"),
        ("name,address\n", "CSV contains no hospital rows"),
        ("name,address\n\n\n", "CSV contains no hospital rows"),
        ("name,phone\nA,555\n", "missing required column(s): address"),
        ("General,1 Main St\n", "missing required column(s): name, address"),
        ("name,address,beds\nA,1 St,4\n", "unknown column(s): beds"),
        ("name,address,name\nA,1 St,B\n", "duplicate columns in header"),
    ],
)
def test_rejects_malformed_files(text: str, message: str) -> None:
    assert file_error(text) == message


def test_rejects_more_than_max_rows() -> None:
    rows = "".join(f"H{i},{i} St\n" for i in range(21))

    assert file_error("name,address\n" + rows) == "CSV contains 21 hospitals; maximum is 20"


def test_rejects_non_utf8() -> None:
    assert file_error("name,address\nCaf\xe9,1 St\n".encode("latin-1")) == (
        "file must be UTF-8 encoded"
    )


@pytest.mark.parametrize("text", ['name,address\nA,"1 St\nB,2 St\n', 'name,address\nA,"1"St\n'])
def test_rejects_broken_quoting(text: str) -> None:
    assert file_error(text).startswith("malformed CSV")
