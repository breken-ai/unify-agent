from __future__ import annotations

from unify import db
from tests.helpers import _handle_project
from unify.function_manager.function_manager import FunctionManager
from unify.guidance_manager.guidance_manager import GuidanceManager


@_handle_project
def test_function_ids_roundtrip_and_inverse_link():
    src_a = (
        "def alpha(a: int) -> int:\n"
        '    """Return value + 1"""\n'
        "    return a + 1\n"
    )
    src_b = (
        "def beta(b: int) -> int:\n" '    """Return value * 2"""\n' "    return b * 2\n"
    )
    fm = FunctionManager()
    fm.add_functions(implementations=[src_a, src_b])
    listing = fm.list_functions()
    alpha_id = listing["alpha"]["function_id"]
    beta_id = listing["beta"]["function_id"]
    assert listing["alpha"]["guidance_ids"] == []

    gm = GuidanceManager()
    out = gm.add_guidance(
        title="Math Ops",
        content="Guidance relevant to alpha and beta functions.",
        function_ids=[alpha_id, beta_id],
    )
    gid = out["details"]["guidance_id"]

    # Roundtrip: the row stores function_ids.
    rows = gm.filter(filter=f"guidance_id = {gid}", limit=1)
    assert rows and rows[0].function_ids == [alpha_id, beta_id]
    assert rows[0].stale_reasons == []

    # Functions report the guidance citing them, derived from function_ids.
    listing = fm.list_functions()
    assert listing["alpha"]["guidance_ids"] == [gid]
    assert listing["beta"]["guidance_ids"] == [gid]

    # The JSON list column is addressable from SQL.
    cited = db.query(
        "SELECT guidance_id FROM guidance"
        " WHERE EXISTS (SELECT 1 FROM json_each(function_ids) WHERE value = ?)",
        (alpha_id,),
    )
    assert [row["guidance_id"] for row in cited] == [gid]


@_handle_project
def test_update_function_ids():
    src_x = "def inc(x: int) -> int:\n" '    """Increment"""\n' "    return x + 1\n"
    src_y = "def dbl(y: int) -> int:\n" '    """Double"""\n' "    return y * 2\n"
    fm = FunctionManager()
    fm.add_functions(implementations=[src_x, src_y])
    listing = fm.list_functions()
    inc_id = listing["inc"]["function_id"]
    dbl_id = listing["dbl"]["function_id"]

    gm = GuidanceManager()
    gid = gm.add_guidance(
        title="Calculations",
        content="Useful operations for math.",
        function_ids=[inc_id, dbl_id],
    )["details"]["guidance_id"]

    gm.update_guidance(guidance_id=gid, function_ids=[inc_id])
    rows = gm.filter(filter=f"guidance_id = {gid}", limit=1)
    assert rows and rows[0].function_ids == [inc_id]

    listing = fm.list_functions()
    assert listing["inc"]["guidance_ids"] == [gid]
    assert listing["dbl"]["guidance_ids"] == []


@_handle_project
def test_update_to_missing_function_records_stale_reason():
    fm = FunctionManager()
    fm.add_functions(
        implementations="def keep(x: int) -> int:\n"
        '    """Keep"""\n'
        "    return x\n",
    )
    keep_id = fm.list_functions()["keep"]["function_id"]

    gm = GuidanceManager()
    gid = gm.add_guidance(title="Links", content="Cites functions.")["details"][
        "guidance_id"
    ]

    gm.update_guidance(guidance_id=gid, function_ids=[keep_id, 424242])
    (row,) = gm.filter(filter=f"guidance_id = {gid}", limit=1)
    assert row.function_ids == [keep_id, 424242]
    (reason,) = row.stale_reasons
    assert reason.dep_kind == "function"
    assert reason.id == 424242

    # reconcile_dependencies re-derives the same debt without duplicating it.
    outcome = gm.reconcile_dependencies()
    assert outcome["details"]["stale_guidance_ids"] == [gid]
    (row,) = gm.filter(filter=f"guidance_id = {gid}", limit=1)
    assert [r.id for r in row.stale_reasons] == [424242]
