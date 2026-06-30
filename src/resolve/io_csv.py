"""CSV read/write for the resolver.

Input contract : company, booth, description, categories
Output contract: company, resolved_url, confidence, candidate_alternatives,
                 needs_review, notes

Headers are matched case-insensitively and tolerant of surrounding whitespace.
Optional input columns may be absent; only `company` is required.
"""

import csv
from typing import Iterable

from src.resolve.models import CompanyInput, ResolutionResult

INPUT_COLUMNS = ["company", "booth", "description", "categories"]
OUTPUT_COLUMNS = [
    "company",
    "resolved_url",
    "confidence",
    "candidate_alternatives",
    "needs_review",
    "notes",
]

# How multiple alternative URLs are joined into the single CSV cell.
ALT_SEPARATOR = "; "


def _normalise_header(name: str) -> str:
    return (name or "").strip().lower()


def read_input_csv(path: str) -> list[CompanyInput]:
    """Read the input CSV into CompanyInput records.

    Raises ValueError if the required `company` column is missing.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return []
        header_map = {_normalise_header(h): h for h in reader.fieldnames}
        if "company" not in header_map:
            raise ValueError(
                f"Input CSV must have a 'company' column; found {reader.fieldnames!r}"
            )

        rows: list[CompanyInput] = []
        for raw in reader:
            def get(col: str) -> str:
                src = header_map.get(col)
                return (raw.get(src, "") or "").strip() if src else ""

            company = get("company")
            if not company:
                continue  # skip blank rows
            rows.append(
                CompanyInput(
                    company=company,
                    booth=get("booth"),
                    description=get("description"),
                    categories=get("categories"),
                )
            )
        return rows


def write_output_csv(path: str, results: Iterable[ResolutionResult]) -> None:
    """Write ResolutionResult records to the fixed output schema."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        for r in results:
            writer.writerow([
                r.company,
                r.resolved_url,
                f"{r.confidence:.3f}",
                ALT_SEPARATOR.join(r.candidate_alternatives),
                "true" if r.needs_review else "false",
                r.notes,
            ])
