"""
Shared helpers for prompt-groups tests (test_groups.py, test_groups_extras.py).

The ``talemate.prompts.groups`` module reads exactly one scene-shaped attribute,
``scene.template_dir``. The tests need to drive arbitrary values into it
(strings, ``Path`` objects, ``None``, or a real path) to exercise the
``isinstance(template_dir, str)`` validation branch in
``get_scene_template_path``.

The base ``Scene.template_dir`` is a ``@property`` that always returns a
``str`` joined from ``save_dir`` + ``"templates"``. To drive the invalid-type
branches, this helper exposes a ``Scene`` subclass that shadows the
``@property`` with a plain class attribute. Renames or removals of
``template_dir`` on the parent will still surface as test failures (the
attribute is still named ``template_dir`` and used in tests via ``scene.template_dir``).
"""

from __future__ import annotations

from typing import Any

from talemate.tale_mate import Scene


class TemplateDirScene(Scene):
    """Real ``Scene`` subclass exposing ``template_dir`` as a settable attribute.

    The shadowed class-level ``template_dir`` (initialized to ``None``) replaces
    the parent's ``@property`` descriptor on this subclass; ``__init__`` then
    accepts whatever value the test wants — ``str``, ``Path``, ``None``, or a
    missing-attribute-equivalent (use ``del scene.template_dir`` to simulate).
    """

    template_dir: Any = None  # type: ignore[assignment]

    def __init__(self, *, template_dir: Any = None):
        super().__init__()
        self.template_dir = template_dir


def make_template_dir_scene(template_dir: Any) -> TemplateDirScene:
    """Build a real ``TemplateDirScene`` with ``template_dir`` set to ``value``."""
    return TemplateDirScene(template_dir=template_dir)
