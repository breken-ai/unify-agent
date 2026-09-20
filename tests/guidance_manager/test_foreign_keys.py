"""
Link tests between guidance and functions.

Coverage
========
✓ guidance.function_ids → functions.function_id
  - Guidance stores the ids it was given
  - Deleting a function pops its id from every guidance row and records
    the loss as a StaleReason
  - An empty function_ids list is valid
  - functions.guidance_ids is derived from guidance.function_ids
"""

from __future__ import annotations

from unify import db
from tests.helpers import _handle_project
from unify.function_manager.function_manager import FunctionManager
from unify.guidance_manager.guidance_manager import GuidanceManager


def _guidance_rows() -> list[dict]:
    rows = db.query(
        "SELECT guidance_id, function_ids, stale_reasons FROM guidance ORDER BY guidance_id",
    )
    return [db.decode(row, db.GUIDANCE_JSON_COLUMNS) for row in rows]


@_handle_project
def test_fk_function_ids_valid_reference():
    """Guidance can reference stored functions by id."""
    gm = GuidanceManager()
    fm = FunctionManager()

    fm.add_functions(
        implementations=[
            "def func1():\n    return 1\n",
            "def func2():\n    return 2\n",
        ],
    )
    func_ids = sorted(
        int(row["function_id"]) for row in db.query("SELECT function_id FROM functions")
    )
    assert len(func_ids) == 2

    gm.add_guidance(
        title="Function Guide",
        content="Guide for functions",
        function_ids=func_ids,
    )

    (row,) = _guidance_rows()
    assert sorted(row["function_ids"]) == func_ids

    # The inverse link is derived at read time from guidance.function_ids.
    listing = fm.list_functions()
    assert listing["func1"]["guidance_ids"] == [row["guidance_id"]]
    assert listing["func2"]["guidance_ids"] == [row["guidance_id"]]


@_handle_project
def test_fk_function_ids_set_null_on_delete():
    """Deleting a function removes it from guidance.function_ids and records why."""
    gm = GuidanceManager()
    fm = FunctionManager()

    for i in range(3):
        fm.add_functions(implementations=f"def func{i}():\n    return {i}\n")
    listing = fm.list_functions()
    f1, f2, f3 = (listing[f"func{i}"]["function_id"] for i in range(3))

    gm.add_guidance(
        title="Multi-Function Guide",
        content="Guide for multiple functions",
        function_ids=[f1, f2, f3],
    )
    (row,) = _guidance_rows()
    assert sorted(row["function_ids"]) == [f1, f2, f3]

    fm.delete_function(function_id=f2)

    (row,) = _guidance_rows()
    assert sorted(row["function_ids"]) == [f1, f3]
    (reason,) = row["stale_reasons"]
    assert reason["dep_kind"] == "function"
    assert reason["id"] == f2
    assert reason["name"] == "func1"

    # The public read carries the same state.
    (guidance,) = gm.filter(filter="is_builtin = 0")
    assert sorted(guidance.function_ids) == [f1, f3]
    assert [r.id for r in guidance.stale_reasons] == [f2]


@_handle_project
def test_fk_function_ids_empty_array():
    """An empty function_ids list is valid."""
    gm = GuidanceManager()

    gm.add_guidance(
        title="Standalone Guide",
        content="Guide without function references",
        function_ids=[],
    )

    (row,) = _guidance_rows()
    assert row["function_ids"] == []
