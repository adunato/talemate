"""
Unit tests for src/talemate/game/engine/nodes/ux.py.

Covers the UX element-builder nodes (`BuildChoiceElement`,
`BuildTextInputElement`, `BuildNoticeElement`) along with `StyleElement` and
the `EmitElement`'s parsing/wait helpers. The async wait-for-interaction
path of `EmitElement.run` is exercised via `wait_for_interaction` directly
with an isolated `GraphState` so we don't need to spin up a real websocket
or scene.

Skipped paths:
- The full `EmitElement.run` for blocking elements: it calls
  `active_scene.get()` then polls until the scene is inactive or a selection
  arrives. We exercise `wait_for_interaction` and `parse_selection_payload`
  directly, plus the non-blocking `notice` `run` path.
"""

import asyncio
import time
import uuid

import pytest

from talemate.emit.signals import handlers as emit_handlers
from talemate.game.engine.nodes.core import (
    GraphContext,
    GraphState,
    InputValueError,
    UNRESOLVED,
)
from talemate.game.engine.nodes.ux import (
    BuildChoiceElement,
    BuildNoticeElement,
    BuildTextInputElement,
    EmitElement,
    StyleElement,
    _parse_ux_element,
)
from talemate.game.engine.ux.schema import (
    UXChoice,
    UXChoiceElement,
    UXNoticeElement,
    UXSelection,
    UXTextInputElement,
)

from conftest import MockScene
from _node_test_helpers import run_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def collected_ux_emissions():
    """Capture every `emit('ux', ...)` call by attaching a Receiver to the
    `ux` signal. Restores any prior receivers on teardown."""
    captured = []

    def _receiver(emission):
        captured.append(emission)

    sig = emit_handlers["ux"]
    sig.connect(_receiver, weak=False)
    try:
        yield captured
    finally:
        sig.disconnect(_receiver)


# ---------------------------------------------------------------------------
# BuildChoiceElement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_choice_element_minimum_inputs():
    """A choice element with just a list of strings normalizes to UXChoice
    objects and produces a usable UX payload."""
    node = BuildChoiceElement()
    out = await run_node(
        node,
        inputs={
            "choices": ["Yes", "No"],
            "title": "Confirm?",
            "body": "Pick one",
        },
    )

    payload = out["ux_element"]
    assert payload["kind"] == "choice"
    assert payload["title"] == "Confirm?"
    assert payload["body"] == "Pick one"
    assert payload["closable"] is True
    assert payload["timeout_seconds"] == 0
    # raw choices are echoed back on the choices output
    assert out["choices"] == ["Yes", "No"]
    # ux_id matches the element id
    assert out["ux_id"] == out["id"] == payload["id"]
    # multi_select default is False
    assert payload["multi_select"] is False
    # at least 2 choices in the rendered element
    assert len(payload["choices"]) == 2


@pytest.mark.asyncio
async def test_build_choice_element_uses_supplied_id_when_provided():
    """A user-supplied id is preserved and not auto-generated."""
    node = BuildChoiceElement()
    out = await run_node(
        node,
        inputs={
            "choices": ["A", "B"],
            "id": "custom-id-123",
            "closable": False,
            "timeout_seconds": 30,
            "multi_select": True,
            "default": "A",
        },
    )

    assert out["id"] == "custom-id-123"
    assert out["ux_element"]["id"] == "custom-id-123"
    assert out["closable"] is False
    assert out["timeout_seconds"] == 30
    assert out["multi_select"] is True
    assert out["default"] == "A"


@pytest.mark.asyncio
async def test_build_choice_element_empty_choices_raises():
    node = BuildChoiceElement()
    with pytest.raises(InputValueError):
        await run_node(node, inputs={"choices": []})


@pytest.mark.asyncio
async def test_build_choice_element_uses_property_title_when_input_blank():
    """The element title falls back to the `element_title` property when no
    title input is supplied."""
    node = BuildChoiceElement()
    node.set_property("element_title", "Property Title")
    node.set_property("element_body", "Property Body")
    out = await run_node(node, inputs={"choices": ["A"]})
    assert out["ux_element"]["title"] == "Property Title"
    assert out["ux_element"]["body"] == "Property Body"


@pytest.mark.asyncio
async def test_build_choice_element_negative_timeout_clamped_to_zero():
    """Negative timeouts must be clamped to 0 (no timeout)."""
    node = BuildChoiceElement()
    out = await run_node(
        node, inputs={"choices": ["A"], "timeout_seconds": -5}
    )
    assert out["timeout_seconds"] == 0


@pytest.mark.asyncio
async def test_build_choice_element_string_numeric_timeout_coerced_to_int():
    """A numeric-string timeout is coerced via int()."""
    node = BuildChoiceElement()
    out = await run_node(
        node, inputs={"choices": ["A"], "timeout_seconds": "42"}
    )
    assert out["timeout_seconds"] == 42


# ---------------------------------------------------------------------------
# BuildTextInputElement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_text_input_element_default_path():
    node = BuildTextInputElement()
    out = await run_node(
        node, inputs={"title": "Name?", "body": "Enter your name"}
    )
    payload = out["ux_element"]
    assert payload["kind"] == "text_input"
    assert payload["title"] == "Name?"
    assert payload["body"] == "Enter your name"
    assert payload["multiline"] is False
    # rows=0 in the property normalizes to None on the element, then 0 on
    # the output socket
    assert out["rows"] == 0
    # placeholder/default empty -> None on the element
    assert payload["placeholder"] is None
    assert payload["default"] is None
    assert out["trim"] is True


@pytest.mark.asyncio
async def test_build_text_input_element_multiline_with_rows_and_placeholder():
    node = BuildTextInputElement()
    out = await run_node(
        node,
        inputs={
            "multiline": True,
            "rows": 5,
            "placeholder": "  type here  ",
            "default": "starter text",
            "trim": False,
        },
    )
    payload = out["ux_element"]
    assert payload["multiline"] is True
    assert payload["rows"] == 5
    # placeholder is stripped
    assert payload["placeholder"] == "type here"
    assert payload["default"] == "starter text"
    assert payload["trim"] is False


@pytest.mark.asyncio
async def test_build_text_input_element_string_numeric_rows_coerced():
    """A numeric-string rows value is coerced via int()."""
    node = BuildTextInputElement()
    out = await run_node(
        node, inputs={"multiline": True, "rows": "7"}
    )
    assert out["rows"] == 7


# ---------------------------------------------------------------------------
# BuildNoticeElement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_notice_element_basic():
    node = BuildNoticeElement()
    out = await run_node(
        node,
        inputs={
            "title": "Heads up",
            "body": "Reactor critical",
            "closable": True,
            "timeout_seconds": 10,
        },
    )
    payload = out["ux_element"]
    assert payload["kind"] == "notice"
    assert payload["title"] == "Heads up"
    assert payload["body"] == "Reactor critical"
    assert payload["closable"] is True
    assert payload["timeout_seconds"] == 10
    # blocking is hardcoded False on UXNoticeElement
    assert payload["blocking"] is False


@pytest.mark.asyncio
async def test_build_notice_element_auto_generated_id_is_uuid():
    """When no id is supplied, the generated id parses as a UUID4."""
    node = BuildNoticeElement()
    out = await run_node(node, inputs={"title": "x", "body": "y"})
    parsed = uuid.UUID(out["id"])
    assert parsed.version == 4


# ---------------------------------------------------------------------------
# StyleElement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_style_element_applies_color_and_icon_to_meta():
    """StyleElement populates element.color/icon and also mirrors them into
    the meta dict for backward compatibility."""
    base = UXChoiceElement(
        id="el-1",
        title="t",
        body="b",
        choices=[UXChoice(id="c0", label="ok", value="ok")],
    )
    out = await run_node(
        StyleElement(),
        inputs={
            "ux_element": base,
            "tint": "primary",
            "icon": "mdi-info",
            "apply_scene_colors": True,
            "compact": True,
        },
    )

    payload = out["ux_element"]
    assert payload["color"] == "primary"
    assert payload["icon"] == "mdi-info"
    assert payload["meta"]["color"] == "primary"
    assert payload["meta"]["icon"] == "mdi-info"
    assert payload["apply_scene_colors"] is True
    assert payload["compact"] is True


@pytest.mark.asyncio
async def test_style_element_clears_meta_when_color_and_icon_empty():
    """Empty inputs translate to None on the element, which removes color
    and icon from the meta dict."""
    base = UXChoiceElement(
        id="el-2",
        title="t",
        body="b",
        choices=[UXChoice(id="c0", label="ok", value="ok")],
        # pre-existing meta entries
        meta={"color": "old", "icon": "mdi-old", "extra": "kept"},
    )
    out = await run_node(
        StyleElement(),
        inputs={
            "ux_element": base,
            "tint": "",
            "icon": "",
        },
    )
    payload = out["ux_element"]
    assert payload["color"] is None
    assert payload["icon"] is None
    # meta loses color/icon but keeps unrelated keys
    assert "color" not in payload["meta"]
    assert "icon" not in payload["meta"]
    assert payload["meta"]["extra"] == "kept"


@pytest.mark.asyncio
async def test_style_element_invalid_payload_raises():
    """Non-parseable ux_element input raises InputValueError."""
    with pytest.raises(InputValueError):
        await run_node(
            StyleElement(),
            inputs={"ux_element": {"definitely": "not a ux element"}},
        )


@pytest.mark.asyncio
async def test_style_element_passes_through_dict_payload():
    """A dict UX element is parsed via the discriminated UXElement adapter."""
    base = {
        "id": "x-1",
        "kind": "notice",
        "title": "T",
        "body": "B",
        "closable": True,
    }
    out = await run_node(
        StyleElement(),
        inputs={"ux_element": base, "tint": "info"},
    )
    assert out["ux_element"]["id"] == "x-1"
    assert out["ux_element"]["color"] == "info"


# ---------------------------------------------------------------------------
# _parse_ux_element helper
# ---------------------------------------------------------------------------


def test_parse_ux_element_returns_existing_object_unchanged():
    obj = UXNoticeElement(id="abc", title="t", body="b")
    assert _parse_ux_element(obj) is obj


def test_parse_ux_element_dispatches_on_kind_for_dict():
    raw = {"id": "txt", "kind": "text_input", "title": "name", "body": "?"}
    parsed = _parse_ux_element(raw)
    assert isinstance(parsed, UXTextInputElement)
    assert parsed.id == "txt"


# ---------------------------------------------------------------------------
# EmitElement.parse_selection_payload
# ---------------------------------------------------------------------------


def test_parse_selection_payload_uxselection_passthrough():
    sel = UXSelection(ux_id="x", kind="choice", selected="a")
    assert EmitElement().parse_selection_payload("x", sel) is sel


def test_parse_selection_payload_dict_with_valid_fields():
    sel = EmitElement().parse_selection_payload(
        "x",
        {"ux_id": "x", "kind": "choice", "selected": "y", "cancelled": False},
    )
    assert isinstance(sel, UXSelection)
    assert sel.ux_id == "x"
    assert sel.selected == "y"


def test_parse_selection_payload_scalar_wraps_in_uxselection():
    sel = EmitElement().parse_selection_payload("xyz", "the answer")
    assert isinstance(sel, UXSelection)
    assert sel.ux_id == "xyz"
    assert sel.selected == "the answer"
    assert sel.kind == "choice"


# ---------------------------------------------------------------------------
# EmitElement.shared_container
# ---------------------------------------------------------------------------


def test_shared_container_prefers_scene_nodegraph_state():
    """When the scene exposes a nodegraph_state with shared, it must win
    over state.shared."""
    scene_shared = {}
    state_shared = {}

    class _SceneStub:
        class _Inner:
            shared = scene_shared

        nodegraph_state = _Inner()

    state = GraphState()
    state.shared = state_shared
    container, label = EmitElement().shared_container(state, _SceneStub())
    assert container is scene_shared
    assert "scene" in label


def test_shared_container_falls_back_to_state_shared_when_scene_lacks_attr():
    state = GraphState()
    state.shared["foo"] = 1

    class _SceneStub:
        pass

    container, label = EmitElement().shared_container(state, _SceneStub())
    assert container is state.shared
    assert label == "state.shared"


# ---------------------------------------------------------------------------
# EmitElement.wait_for_interaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_interaction_returns_selection_when_present():
    """If the shared bucket already contains a UXSelection-shaped payload,
    `wait_for_interaction` returns immediately with the parsed selection."""
    node = EmitElement()

    class _SceneStub:
        active = True

    state = GraphState()
    # Pre-seed the selection
    state.shared[node.ux_shared_key("ux-1")] = {
        "ux_id": "ux-1",
        "kind": "choice",
        "selected": "yes",
    }

    selection, timed_out, aborted = await node.wait_for_interaction(
        state, "ux-1", _SceneStub(), timeout_seconds=0, start_time=time.time()
    )
    assert isinstance(selection, UXSelection)
    assert selection.selected == "yes"
    assert timed_out is False
    assert aborted is False


@pytest.mark.asyncio
async def test_wait_for_interaction_respects_timeout_when_no_selection():
    """When no selection arrives, the configured timeout fires and returns
    (None, True, False)."""
    node = EmitElement()
    # speed up the polling so the test doesn't take 0.25s
    node._wait_sleep_seconds = 0.01

    class _SceneStub:
        active = True

    state = GraphState()
    selection, timed_out, aborted = await node.wait_for_interaction(
        state,
        "ux-2",
        _SceneStub(),
        timeout_seconds=1,
        start_time=time.time() - 5,  # already expired
    )
    assert selection is None
    assert timed_out is True
    assert aborted is False
    # marker cleaned up
    assert node.ux_shared_key("ux-2") not in state.shared


@pytest.mark.asyncio
async def test_wait_for_interaction_aborts_when_scene_becomes_inactive():
    """A deactivated scene cancels the wait without timing out."""
    node = EmitElement()
    node._wait_sleep_seconds = 0.01

    class _SceneStub:
        active = False  # already inactive on entry

    state = GraphState()
    selection, timed_out, aborted = await node.wait_for_interaction(
        state, "ux-3", _SceneStub(), timeout_seconds=10, start_time=time.time()
    )
    assert selection is None
    assert aborted is True
    assert timed_out is False


@pytest.mark.asyncio
async def test_wait_for_interaction_picks_up_late_arriving_selection():
    """Selection injected mid-wait is observed and returned."""
    node = EmitElement()
    node._wait_sleep_seconds = 0.01

    class _SceneStub:
        active = True

    state = GraphState()

    async def _inject_after_delay():
        await asyncio.sleep(0.05)
        state.shared[node.ux_shared_key("ux-4")] = UXSelection(
            ux_id="ux-4", kind="choice", selected="late"
        )

    # Inject in parallel with the wait
    inject_task = asyncio.create_task(_inject_after_delay())
    selection, timed_out, aborted = await node.wait_for_interaction(
        state, "ux-4", _SceneStub(), timeout_seconds=5, start_time=time.time()
    )
    await inject_task
    assert isinstance(selection, UXSelection)
    assert selection.selected == "late"
    assert timed_out is False
    assert aborted is False


# ---------------------------------------------------------------------------
# EmitElement.run for non-blocking notice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_element_notice_is_fire_and_forget(collected_ux_emissions):
    """A notice element does NOT wait for selection — it emits a single
    `present` action and returns with default outputs."""
    notice = UXNoticeElement(
        id="notice-1",
        title="Heads up",
        body="all good",
        timeout_seconds=0,
    )
    out = await run_node(
        EmitElement(), inputs={"ux_element": notice}
    )
    # No interaction performed
    assert out["cancelled"] is False
    assert out["timed_out"] is False
    assert out["value"] is None
    # ux_id propagated
    assert out["ux_id"] == "notice-1"
    # exactly one `present` emission was sent (no close)
    actions = [
        e.data["action"]
        for e in collected_ux_emissions
        if e.id == "notice-1"
    ]
    assert actions == ["present"]


@pytest.mark.asyncio
async def test_emit_element_invalid_payload_raises_input_value_error():
    """An unparseable ux_element raises InputValueError."""
    with pytest.raises(InputValueError):
        await run_node(
            EmitElement(),
            inputs={"ux_element": {"missing": "kind"}},
        )


@pytest.mark.asyncio
async def test_emit_element_choice_uses_seeded_selection(collected_ux_emissions):
    """Pre-seed a selection in the scene's shared bucket; EmitElement emits
    `present` + `close` and surfaces the selected value."""
    scene = MockScene()
    # provide nodegraph_state.shared (where router writes selections)
    scene.nodegraph_state = GraphState()

    choice = UXChoiceElement(
        id="ch-1",
        title="t",
        body="b",
        choices=[
            UXChoice(id="c-yes", label="Yes", value="yes"),
            UXChoice(id="c-no", label="No", value="no"),
        ],
        timeout_seconds=0,
    )

    node = EmitElement()
    node._wait_sleep_seconds = 0.01

    # Pre-seed on the path EmitElement actually consults — the SCENE'S shared
    scene.nodegraph_state.shared[node.ux_shared_key("ch-1")] = UXSelection(
        ux_id="ch-1",
        kind="choice",
        selected="yes",
        choice_id="c-yes",
        label="Yes",
    )

    out = await run_node(
        node, scene=scene, inputs={"ux_element": choice}
    )

    assert out["cancelled"] is False
    assert out["timed_out"] is False
    assert out["value"] == "yes"
    assert out["values"]["choice_id"] == "c-yes"
    assert out["values"]["label"] == "Yes"
    # both present & close emitted for ch-1
    actions = [
        e.data["action"]
        for e in collected_ux_emissions
        if e.id == "ch-1"
    ]
    assert actions == ["present", "close"]
