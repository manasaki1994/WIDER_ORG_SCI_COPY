"""Excel exports (Web of Science .xls/.xlsx, ProQuest .xls) as tag -> values records.

The spreadsheet libraries are optional (``pip install "wider[import]"``): ``openpyxl`` for .xlsx and ``xlrd``
for the legacy .xls format. They are imported lazily so the rest of the package never needs them.
"""
from __future__ import annotations

from pathlib import Path

INSTALL_HINT = 'install the optional import extras:  pip install "wider[import]"'


class MissingDependency(ImportError):
    """A reader needs a package that is not installed; the message says which and how to add it."""


def _rows_to_records(rows: list[list]) -> tuple[list[str], list[dict[str, list[str]]]]:
    rows = [[("" if c is None else str(c).strip()) for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return [], []
    headers = rows[0]
    out: list[dict[str, list[str]]] = []
    for cells in rows[1:]:
        rec: dict[str, list[str]] = {}
        for h, v in zip(headers, cells):
            if h and v:
                rec.setdefault(h, []).append(v)
        if rec:
            out.append(rec)
    return headers, out


def _looks_like_html(path: Path) -> bool:
    head = path.read_bytes()[:512].lstrip().lower()
    return head.startswith(b"<")


def read_xlsx_table(path: str | Path) -> tuple[list[str], list[dict[str, list[str]]]]:
    """First worksheet of an .xlsx file; the first non-empty row is the header."""
    path = Path(path)
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise MissingDependency(f"reading {path.name} needs openpyxl; {INSTALL_HINT}") from e
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    return _rows_to_records(rows)


def read_xls_table(path: str | Path) -> tuple[list[str], list[dict[str, list[str]]]]:
    """First sheet of a legacy .xls file (BIFF). An HTML table saved as .xls is refused with a hint."""
    path = Path(path)
    if _looks_like_html(path):
        raise ValueError(f"{path.name} is an HTML table saved with an .xls name; export CSV or a real .xlsx instead")
    try:
        import xlrd
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise MissingDependency(f"reading {path.name} needs xlrd; {INSTALL_HINT}") from e
    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    rows = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
    rows = [[(int(c) if isinstance(c, float) and c.is_integer() else c) for c in r] for r in rows]
    return _rows_to_records(rows)


def read_excel_table(path: str | Path) -> tuple[list[str], list[dict[str, list[str]]]]:
    path = Path(path)
    if path.suffix.lower() == ".xls":
        return read_xls_table(path)
    return read_xlsx_table(path)


def read_excel(path: str | Path) -> list[dict[str, list[str]]]:
    return read_excel_table(path)[1]
