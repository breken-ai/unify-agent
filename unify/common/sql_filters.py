"""Composing and reporting on the SQL ``WHERE`` clauses the libraries read with.

The skill libraries take their filters as SQL clauses: from the model (a
tool's ``filter`` argument, a nested actor's discovery scope) and from the
runtime (a manager's ``filter_scope``, id exclusions). Clauses are combined
with ``AND`` here, and a clause SQLite rejects becomes a tool error that
names the columns the clause may use.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .tool_outcome import ToolErrorException


def and_clauses(*clauses: Optional[str]) -> Optional[str]:
    """Join the non-empty clauses with ``AND``; ``None`` when there are none."""
    parts = [clause for clause in clauses if clause]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return " AND ".join(f"({part})" for part in parts)


def or_clauses(*clauses: Optional[str]) -> Optional[str]:
    parts = [clause for clause in clauses if clause]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return " OR ".join(f"({part})" for part in parts)


def not_in(column: str, ids: Optional[Sequence[int]]) -> Optional[str]:
    """``column NOT IN (...)`` for the given ids; ``None`` when there are none."""
    if not ids:
        return None
    return f"{column} NOT IN ({', '.join(str(int(value)) for value in sorted(ids))})"


def invalid_filter_error(
    exc: Exception,
    filter: Optional[str],
    columns: Sequence[str],
) -> ToolErrorException:
    """Translate a clause SQLite rejected into an actionable tool error."""
    return ToolErrorException(
        {
            "error_kind": "invalid_filter",
            "message": (
                f"filter {filter!r} was rejected: {exc}. The filter is a SQL "
                f"WHERE clause (without the WHERE keyword) over the columns "
                f"{', '.join(columns)}."
            ),
            "details": {"filter": filter, "columns": list(columns)},
        },
    )


__all__ = ["and_clauses", "invalid_filter_error", "not_in", "or_clauses"]
