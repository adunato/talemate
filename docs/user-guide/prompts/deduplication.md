# Prompt Deduplication

Talemate runs a line-level fuzzy-dedupe pass over every rendered prompt before it is sent to the language model. The pass removes lines longer than 32 characters whose similarity to an earlier line is ≥95%, which saves tokens when the same paragraph slips into a prompt from multiple context sources (world info, memories, pins).

In most cases this is exactly what you want. Occasionally you need to turn it off.

## When to disable it

Disable dedupe when your template contains **structured repeated content** that must reach the model intact. The canonical case is a beat listing where the beats happen to share long near-identical prefixes:

```
Beat 1: Alice confronts Bob about the missing ledger.
Beat 2: Alice confronts Bob about the missing ring.
Beat 3: Alice confronts Bob about the missing key.
```

With dedupe on, the similarity threshold collapses these into one line and the model loses the plan. With dedupe off the whole list reaches the prompt verbatim.

## How to disable it

There are two control points. Pick whichever is closer to the content you're protecting.

### From inside a template

Call `disable_dedupe()` once at the top of the template (or anywhere before the structured content):

```jinja2
{{ disable_dedupe() }}
<|SECTION:BEATS|>
Beat 1: Alice confronts Bob about the missing ledger.
Beat 2: Alice confronts Bob about the missing ring.
Beat 3: Alice confronts Bob about the missing key.
<|CLOSE_SECTION|>
```

This is the usual choice when the template itself owns the repeated structure. See the [`disable_dedupe()` function reference](../node-editor/reference/template_functions.md#disable_dedupe) for the full function entry.

### From a `Prompt from Template` node

Set the node's `dedupe` property to `false`. This toggles deduplication for the prompt produced by that specific node only. Use this when you don't own the template file but still need to opt out for a specific invocation. See the node's [Properties reference](../node-editor/core-concepts/prompt-templates.md#prompt-from-template) for full details.

## Scope and interactions

- Disabling dedupe in one place does **not** cascade. A `disable_dedupe()` call only affects the prompt it is rendered into, and the node's `dedupe` property only affects that node's output.
- Dedupe is separate from the [`condensed`](../node-editor/reference/template_functions.md) template filter. The condensed filter uses its own marker-based mechanism to collapse multi-line context for comparison and does not rely on the dedupe pass. Disabling dedupe leaves the condensed filter's behavior unchanged.
