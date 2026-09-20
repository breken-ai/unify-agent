"""Tests for FunctionManager environment exclusion (function_id masking).

Verifies:
1. ToolMetadata supports function_id + function_context
2. ToolSurfaceRegistry.get_function_id() matches collect_primitives() IDs
3. ActorEnvironment populates function_id/function_context for each primitive
4. FunctionManager scope clause generation (stored functions vs primitives)
"""

import pytest
from types import SimpleNamespace

from unify.actor.environments import ActorEnvironment
from unify.actor.environments.base import ToolMetadata
from unify.function_manager.function_manager import FunctionManager
from unify.function_manager.primitives.scope import PrimitiveScope
from unify.function_manager.primitives.registry import get_registry, _get_stable_id

_ACTOR_ACT = "primitives.actor.act"
_ACTOR_CLASS_PATH = "unify.actor.environments.actor._ActorRunner"

# ────────────────────────────────────────────────────────────────────────────
# ToolMetadata function_id + function_context fields
# ────────────────────────────────────────────────────────────────────────────


def test_tool_metadata_function_id_defaults_to_none():
    """function_id defaults to None when not specified."""
    meta = ToolMetadata(name="foo", is_impure=True)
    assert meta.function_id is None
    assert meta.function_context is None


def test_tool_metadata_function_id_with_context():
    """function_id and function_context can be set together."""
    meta = ToolMetadata(
        name="foo",
        is_impure=True,
        function_id=42,
        function_context="primitive",
    )
    assert meta.function_id == 42
    assert meta.function_context == "primitive"

    meta2 = ToolMetadata(
        name="bar",
        is_impure=False,
        function_id=7,
        function_context="compositional",
    )
    assert meta2.function_id == 7
    assert meta2.function_context == "compositional"


# ────────────────────────────────────────────────────────────────────────────
# Registry get_function_id
# ────────────────────────────────────────────────────────────────────────────


def test_registry_get_function_id_actor_act():
    """get_function_id hashes the short class name and method, like _get_stable_id."""
    registry = get_registry()
    fid = registry.get_function_id("actor", "act")
    assert fid == _get_stable_id("_ActorRunner", "act")
    assert fid != _get_stable_id("_ActorRunner", "other")
    assert 0 <= fid <= 0x7FFFFFFF


def test_registry_get_function_id_invalid_alias():
    """get_function_id raises ValueError for unknown alias."""
    registry = get_registry()
    with pytest.raises(ValueError, match="Unknown manager alias"):
        registry.get_function_id("nonexistent_manager", "ask")


def test_registry_get_function_id_matches_collect_primitives():
    """get_function_id produces the same IDs as collect_primitives."""
    registry = get_registry()
    scope = PrimitiveScope.all_managers()
    collected = registry.collect_primitives(scope)
    assert collected

    for name, row in collected.items():
        parts = name.split(".")
        assert len(parts) == 3 and parts[0] == "primitives"
        alias, method = parts[1], parts[2]
        fid = registry.get_function_id(alias, method)
        assert fid == row["function_id"], (
            f"get_function_id({alias!r}, {method!r}) = {fid} "
            f"but collect_primitives has {row['function_id']} for {name}"
        )


# ────────────────────────────────────────────────────────────────────────────
# ActorEnvironment function_id + function_context population
# ────────────────────────────────────────────────────────────────────────────


def test_actor_env_get_tools_has_function_ids():
    """Every tool from ActorEnvironment has function_id and function_context."""
    env = ActorEnvironment()
    tools = env.get_tools()
    assert len(tools) > 0, "Expected at least some tools"
    for fq_name, meta in tools.items():
        assert meta.function_id is not None, f"Tool {fq_name} should have a function_id"
        assert isinstance(meta.function_id, int)
        assert (
            meta.function_context == "primitive"
        ), f"Tool {fq_name} should have function_context='primitive'"
        assert meta.is_impure is True
        assert meta.is_steerable is True


def test_actor_env_function_ids_match_registry():
    """function_ids from get_tools() match registry.get_function_id()."""
    registry = get_registry()
    env = ActorEnvironment()
    tools = env.get_tools()

    for fq_name, meta in tools.items():
        parts = fq_name.split(".")
        assert len(parts) == 3 and parts[0] == "primitives"
        alias, method = parts[1], parts[2]
        expected_id = registry.get_function_id(alias, method)
        assert (
            meta.function_id == expected_id
        ), f"Tool {fq_name}: function_id={meta.function_id}, expected {expected_id}"


def test_actor_env_allowed_methods_still_populates_function_ids():
    """An ActorEnvironment narrowed by allowed_methods keeps tagging its tools."""
    env = ActorEnvironment(allowed_methods={_ACTOR_ACT})
    tools = env.get_tools()

    assert set(tools) == {_ACTOR_ACT}
    meta = tools[_ACTOR_ACT]
    assert meta.function_id == get_registry().get_function_id("actor", "act")
    assert meta.function_context == "primitive"


# ────────────────────────────────────────────────────────────────────────────
# FunctionManager scope clauses
# ────────────────────────────────────────────────────────────────────────────


def _make_fm_stub(
    *,
    filter_scope=None,
    exclude_primitive_ids=None,
    exclude_compositional_ids=None,
    primitive_scope=None,
    include_primitives=True,
):
    """Create a minimal stub that has the real FM scope methods bound."""
    ns = SimpleNamespace(
        _filter_scope=filter_scope,
        _exclude_primitive_ids=(
            frozenset(exclude_primitive_ids) if exclude_primitive_ids else None
        ),
        _exclude_compositional_ids=(
            frozenset(exclude_compositional_ids) if exclude_compositional_ids else None
        ),
        _primitive_scope=primitive_scope,
        _include_primitives=include_primitives,
        _registry=get_registry(),
    )
    ns._compositional_scope = lambda cf=None: FunctionManager._compositional_scope(
        ns,
        cf,
    )
    ns._discovery_scope = lambda cf=None: FunctionManager._discovery_scope(ns, cf)
    ns._scoped_primitive_filter = (
        lambda cf=None: FunctionManager._scoped_primitive_filter(ns, cf)
    )
    return ns


# ── _compositional_scope (stored functions) ─────────────────────────────


def test_compositional_scope_includes_compositional_exclusion():
    """The compositional clause carries the caller filter, the filter_scope
    and the compositional id exclusion."""
    fm = _make_fm_stub(
        filter_scope="docstring LIKE '%data%'",
        exclude_compositional_ids={42},
    )
    result = fm._compositional_scope("name = 'foo'")
    assert result == (
        "(is_primitive = 0) AND (name = 'foo') AND (docstring LIKE '%data%')"
        " AND (function_id NOT IN (42))"
    )


def test_compositional_scope_ignores_primitive_exclusion():
    """Primitive ids are hash-based and could collide with stored ids, so the
    compositional clause never excludes them."""
    fm = _make_fm_stub(
        filter_scope="docstring LIKE '%data%'",
        exclude_primitive_ids={42},
    )
    result = fm._compositional_scope("name = 'foo'")
    assert "function_id" not in result


def test_compositional_scope_filter_scope_only():
    fm = _make_fm_stub(filter_scope="docstring LIKE '%data%'")
    assert fm._compositional_scope() == (
        "(is_primitive = 0) AND (docstring LIKE '%data%')"
    )


def test_compositional_scope_exclusion_ids_sorted():
    fm = _make_fm_stub(exclude_compositional_ids={30, 10, 20})
    assert fm._compositional_scope() == (
        "(is_primitive = 0) AND (function_id NOT IN (10, 20, 30))"
    )


def test_compositional_scope_bare_selects_stored_functions_only():
    fm = _make_fm_stub()
    assert fm._compositional_scope() == "is_primitive = 0"


# ── _scoped_primitive_filter (primitives) ───────────────────────────────


def test_scoped_primitive_filter_with_exclusion():
    """The primitive clause combines primitive_row_filter with the primitive
    id exclusion."""
    scope = PrimitiveScope.single("actor")
    fm = _make_fm_stub(exclude_primitive_ids={42}, primitive_scope=scope)
    result = fm._scoped_primitive_filter()
    assert result == (
        f"(is_primitive = 1) AND ({get_registry().primitive_row_filter(scope)})"
        " AND (function_id NOT IN (42))"
    )


def test_scoped_primitive_filter_ignores_compositional_exclusion():
    scope = PrimitiveScope.single("actor")
    fm = _make_fm_stub(exclude_compositional_ids={42}, primitive_scope=scope)
    result = fm._scoped_primitive_filter()
    assert result == (
        f"(is_primitive = 1) AND ({get_registry().primitive_row_filter(scope)})"
    )


def test_scoped_primitive_filter_ignores_filter_scope():
    """filter_scope narrows stored functions, never the primitive surface."""
    scope = PrimitiveScope.single("actor")
    fm = _make_fm_stub(filter_scope="docstring LIKE '%data%'", primitive_scope=scope)
    assert "docstring" not in fm._scoped_primitive_filter()


def test_primitive_row_filter_is_a_class_membership_clause():
    registry = get_registry()
    assert registry.primitive_row_filter(PrimitiveScope.single("actor")) == (
        f"primitive_class IN ('{_ACTOR_CLASS_PATH}')"
    )


# ── _discovery_scope (both populations) ─────────────────────────────────


def test_discovery_scope_unions_populations_under_caller_filter():
    scope = PrimitiveScope.single("actor")
    fm = _make_fm_stub(
        filter_scope="docstring LIKE '%data%'",
        exclude_compositional_ids={7},
        exclude_primitive_ids={42},
        primitive_scope=scope,
    )
    assert fm._discovery_scope("name LIKE 'get_%'") == (
        "(name LIKE 'get_%') AND ("
        f"({fm._compositional_scope()}) OR ({fm._scoped_primitive_filter()}))"
    )


def test_discovery_scope_without_primitives_is_the_compositional_scope():
    fm = _make_fm_stub(
        filter_scope="docstring LIKE '%data%'",
        primitive_scope=PrimitiveScope.single("actor"),
        include_primitives=False,
    )
    assert fm._discovery_scope() == fm._compositional_scope()
