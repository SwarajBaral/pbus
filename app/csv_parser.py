import csv
import io

from app.schemas import HospitalRow, RowError, ValidationReport

REQUIRED_COLUMNS = ("name", "address")
OPTIONAL_COLUMNS = ("phone",)


class CSVFormatError(ValueError):
    pass


def parse_hospitals(content: bytes, max_rows: int) -> ValidationReport:
    try:
        records = _read_records(content, max_rows)
    except CSVFormatError as exc:
        return ValidationReport(
            valid=False, total_rows=0, rows=[], errors=[RowError(row=None, message=str(exc))]
        )

    columns, data = records
    rows: list[HospitalRow] = []
    errors: list[RowError] = []
    for number, record in enumerate(data, start=1):
        try:
            rows.append(_parse_row(number, columns, record))
        except CSVFormatError as exc:
            errors.append(RowError(row=number, message=str(exc)))

    return ValidationReport(valid=not errors, total_rows=len(data), rows=rows, errors=errors)


def _read_records(content: bytes, max_rows: int) -> tuple[list[str], list[list[str]]]:
    try:
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        header = next(reader, None)
        data = [record for record in reader if any(cell.strip() for cell in record)]
    except UnicodeDecodeError as exc:
        raise CSVFormatError("file must be UTF-8 encoded") from exc
    except csv.Error as exc:
        raise CSVFormatError(f"malformed CSV: {exc}") from exc

    columns = _parse_header(header)
    if not data:
        raise CSVFormatError("CSV contains no hospital rows")
    if len(data) > max_rows:
        raise CSVFormatError(f"CSV contains {len(data)} hospitals; maximum is {max_rows}")
    return columns, data


def _parse_header(header: list[str] | None) -> list[str]:
    if not header:
        raise CSVFormatError("CSV is empty")

    columns = [column.strip().lower() for column in header]
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    unknown = [c for c in columns if c not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]

    if missing:
        raise CSVFormatError(f"missing required column(s): {', '.join(missing)}")
    if unknown:
        raise CSVFormatError(f"unknown column(s): {', '.join(unknown)}")
    if len(set(columns)) != len(columns):
        raise CSVFormatError("duplicate columns in header")
    return columns


def _parse_row(number: int, columns: list[str], record: list[str]) -> HospitalRow:
    if len(record) > len(columns):
        raise CSVFormatError(f"expected at most {len(columns)} fields, got {len(record)}")

    values = {column: cell.strip() for column, cell in zip(columns, record, strict=False)}
    missing = [c for c in REQUIRED_COLUMNS if not values.get(c)]
    if missing:
        raise CSVFormatError(f"missing value for: {', '.join(missing)}")

    return HospitalRow(
        row=number,
        name=values["name"],
        address=values["address"],
        phone=values.get("phone") or None,
    )
