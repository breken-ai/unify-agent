"""
The function -> guidance link on FunctionManager rows.

``guidance_ids`` on a function row is never stored: it is derived at read
time from every ``guidance`` row whose ``function_ids`` cites the function.
Creating guidance that cites a function makes the id appear; deleting the
guidance makes it disappear; nothing has to be cascaded.
"""

from __future__ import annotations

from unify import db
from tests.helpers import _handle_project
from unify.function_manager.function_manager import FunctionManager
from unify.guidance_manager.guidance_manager import GuidanceManager


def _add(fm: FunctionManager, name: str) -> int:
    fm.add_functions(implementations=f"def {name}():\n    return '{name}'\n")
    return fm.list_functions()[name]["function_id"]


def _guidance_ids(fm: FunctionManager, function_id: int) -> list[int]:
    return fm._get_log_by_function_id(function_id=function_id)["guidance_ids"]


@_handle_project
def test_guidance_ids_derived_from_guidance_function_ids():
    gm = GuidanceManager()
    fm = FunctionManager()
    fid = _add(fm, "setup_demo")

    g1 = gm.add_guidance(
        title="Setup Guide",
        content="How to setup the system",
        function_ids=[fid],
    )["details"]["guidance_id"]
    g2 = gm.add_guidance(
        title="Usage Guide",
        content="How to use the system",
        function_ids=[fid],
    )["details"]["guidance_id"]

    assert _guidance_ids(fm, fid) == sorted([g1, g2])
    [row] = fm.filter_functions(filter="name = 'setup_demo'")
    assert row["guidance_ids"] == sorted([g1, g2])
    assert fm.list_functions()["setup_demo"]["guidance_ids"] == sorted([g1, g2])

    # Nothing is written to the functions table for the link.
    columns = {col["name"] for col in db.query("PRAGMA table_info(functions)")}
    assert "guidance_ids" not in columns


@_handle_project
def test_deleting_guidance_removes_it_from_guidance_ids():
    gm = GuidanceManager()
    fm = FunctionManager()
    fid = _add(fm, "complex_setup")

    g1, g2, g3 = (
        gm.add_guidance(title=f"Guide {i}", content=f"Content {i}", function_ids=[fid])[
            "details"
        ]["guidance_id"]
        for i in range(3)
    )
    assert _guidance_ids(fm, fid) == [g1, g2, g3]

    gm.delete_guidance(guidance_id=g2)

    assert _guidance_ids(fm, fid) == [g1, g3]


@_handle_project
def test_function_without_citations_has_empty_guidance_ids():
    fm = FunctionManager()
    fid = _add(fm, "standalone")

    assert _guidance_ids(fm, fid) == []
    [row] = fm.filter_functions(filter="name = 'standalone'")
    assert row["guidance_ids"] == []


@_handle_project
def test_guidance_ids_track_sequential_deletes():
    gm = GuidanceManager()
    fm = FunctionManager()
    fid = _add(fm, "mega_func")

    g_ids = [
        gm.add_guidance(title=f"Guide {i}", content=f"Content {i}", function_ids=[fid])[
            "details"
        ]["guidance_id"]
        for i in range(5)
    ]
    assert _guidance_ids(fm, fid) == g_ids

    for gid in g_ids[:3]:
        gm.delete_guidance(guidance_id=gid)

    assert _guidance_ids(fm, fid) == g_ids[3:]


@_handle_project
def test_updating_guidance_function_ids_relinks():
    gm = GuidanceManager()
    fm = FunctionManager()
    first = _add(fm, "first")
    second = _add(fm, "second")

    gid = gm.add_guidance(
        title="Guide",
        content="Cites first.",
        function_ids=[first],
    )[
        "details"
    ]["guidance_id"]
    assert _guidance_ids(fm, first) == [gid]
    assert _guidance_ids(fm, second) == []

    gm.update_guidance(guidance_id=gid, function_ids=[second])

    assert _guidance_ids(fm, first) == []
    assert _guidance_ids(fm, second) == [gid]


@_handle_project
def test_primitive_rows_never_carry_guidance_ids():
    fm = FunctionManager()
    for row in fm.filter_functions(filter="is_primitive = 1"):
        assert row["guidance_ids"] == []
