from __future__ import annotations

import pytest

from unify import db
from unify.guidance_manager.guidance_manager import GuidanceManager
from unify.guidance_manager.types.guidance import Guidance
from tests.helpers import _handle_project


def test_guidance_legacy_null_is_builtin_normalizes_to_false():
    row = Guidance(
        title="Legacy guidance",
        content="Created before builtins.",
        is_builtin=None,
    )

    assert row.is_builtin is False


@_handle_project
def test_create():
    gm = GuidanceManager()
    out = gm.add_guidance(
        title="Setup demo",
        content="Steps to set up the product demo.",
    )
    gid = out["details"]["guidance_id"]

    rows = gm.filter(filter=f"guidance_id = {gid}")
    assert rows and rows[0].guidance_id == gid
    assert rows[0].title == "Setup demo"
    assert rows[0].content.startswith("Steps to set up")
    assert rows[0].function_ids == []
    assert rows[0].is_builtin is False

    (stored,) = db.query("SELECT title, content, function_ids FROM guidance")
    assert stored["title"] == "Setup demo"
    assert db.loads(stored["function_ids"]) == []


@_handle_project
def test_update():
    gm = GuidanceManager()
    gid = gm.add_guidance(
        title="Onboarding Overview",
        content="We walk through onboarding steps.",
    )["details"]["guidance_id"]

    gm.update_guidance(
        guidance_id=gid,
        content="Updated walkthrough of onboarding steps for new users.",
    )

    rows = gm.filter(filter=f"guidance_id = {gid}")
    assert rows and rows[0].guidance_id == gid
    assert "Updated walkthrough" in rows[0].content
    assert rows[0].title == "Onboarding Overview"


@_handle_project
def test_delete():
    gm = GuidanceManager()
    gid = gm.add_guidance(
        title="Billing",
        content="Explains invoices and payment flows.",
    )["details"]["guidance_id"]

    # ensure present
    assert gm.filter(filter=f"guidance_id = {gid}")

    gm.delete_guidance(guidance_id=gid)
    assert len(gm.filter(filter=f"guidance_id = {gid}")) == 0

    with pytest.raises(ValueError, match="No guidance found"):
        gm.delete_guidance(guidance_id=gid)


@_handle_project
def test_filter_by_title_and_substring():
    gm = GuidanceManager()
    gm.add_guidance(title="Comms", content="Prefer emails for updates")
    gm.add_guidance(title="Ops", content="Runbooks and SOPs")

    rows = gm.filter(filter="title = 'Comms'")
    assert [row.title for row in rows] == ["Comms"]

    rows = gm.filter(filter="content LIKE '%runbooks%' AND is_builtin = 0")
    assert [row.title for row in rows] == ["Ops"]


@_handle_project
def test_add_requires_title_or_content():
    gm = GuidanceManager()

    with pytest.raises(ValueError):
        gm.add_guidance()


@_handle_project
def test_update_requires_a_field():
    gm = GuidanceManager()
    gid = gm.add_guidance(
        title="Docs",
        content="Documentation structure and guidelines.",
    )["details"]["guidance_id"]

    with pytest.raises(ValueError):
        gm.update_guidance(guidance_id=gid)


@_handle_project
def test_clear():
    gm = GuidanceManager()
    builtin_count = len(gm.filter(filter="is_builtin = 1", limit=100))

    out1 = gm.add_guidance(title="Alpha", content="First entry")
    out2 = gm.add_guidance(title="Beta", content="Second entry")
    gid1 = out1["details"]["guidance_id"]
    gid2 = out2["details"]["guidance_id"]

    assert gm.filter(filter=f"guidance_id = {gid1}")
    assert gm.filter(filter=f"guidance_id = {gid2}")

    gm.clear()

    assert gm.filter(filter=f"guidance_id = {gid1}") == []
    assert gm.filter(filter=f"guidance_id = {gid2}") == []
    assert db.query("SELECT 1 FROM guidance") == []
    # The builtins catalogue is not the assistant's to clear.
    assert len(gm.filter(filter="is_builtin = 1", limit=100)) == builtin_count
