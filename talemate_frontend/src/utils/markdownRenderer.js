// Shared vanilla markdown renderer for scene-message components that render
// plain markdown (no scene-parser extensions, no color injection). Using a
// single instance keeps options consistent across components — callers that
// want scene-aware parsing should use SceneTextParser instead.

import { Marked } from "marked";

const renderer = new Marked({ breaks: true, gfm: true });

export function parseBlock(text) {
  return renderer.parse(text ?? "");
}

export function parseInline(text) {
  return renderer.parseInline(text ?? "");
}
