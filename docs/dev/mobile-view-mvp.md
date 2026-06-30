# Mobile View MVP

## Goal

Provide a limited mobile-friendly Talemate view for phones and narrow tablets without redesigning the full desktop application.

The MVP should support:

- Opening Talemate from another device on the same network when launched through `start.bat`.
- Reading and sending scene chat.
- Opening a right-side character panel.
- Opening a right-side agent panel.
- Minimal top navigation for the supported mobile views.

The MVP should not try to make the World Editor, node editor, package manager, prompt editor, templates view, debug tools, or full application settings mobile-complete.

## Non-goals

- No duplicated chat implementation.
- No duplicated character activation implementation.
- No duplicated agent/client settings implementation.
- No separate mobile-only websocket store.
- No mobile support for complex authoring screens in this phase.
- No new backend API unless a concrete gap appears during implementation.

## Access From Mobile Devices

The Windows launcher must expose both the backend and frontend beyond `localhost`.

Current `start.bat` already does this:

```batch
if "%TALEMATE_BACKEND_PORT%"=="" set TALEMATE_BACKEND_PORT=5050
if "%TALEMATE_FRONTEND_PORT%"=="" set TALEMATE_FRONTEND_PORT=8082
embedded_python\python.exe -m uv run src\talemate\server\run.py runserver --host 0.0.0.0 --port %TALEMATE_BACKEND_PORT% --frontend-host 0.0.0.0 --frontend-port %TALEMATE_FRONTEND_PORT%
```

For the mobile MVP, keep this behavior. A phone on the same LAN should open:

```text
http://<server-name-or-lan-ip>:8082
```

If hostname resolution is unreliable, use the server's LAN IP. If the backend port is changed from `5050`, also set `VITE_TALEMATE_BACKEND_WEBSOCKET_URL` so the browser can connect to the correct websocket endpoint.

## UX Scope

### Chat View

The mobile chat view should reuse the existing scene interaction components:

- `SceneMessages`
- `SceneTools`
- `SceneMessageInput`
- `AgentActivityBar`
- `CharacterSheet`

These components are already mounted together inside `TalemateApp.vue`. The mobile implementation should change the surrounding shell and layout only.

Expected mobile behavior:

- Scene messages fill the primary viewport.
- Scene tools remain available, but may wrap or collapse more aggressively.
- The message input stays reachable at the bottom of the chat flow.
- Busy, waiting-for-input, autocomplete, act-as, directed input, audio, and visual status should continue using the existing state and handlers.

### Chat Toolbar

The existing chat toolbar is `SceneTools`. Treat it as part of the mobile chat surface, not as desktop-only navigation.

`SceneTools` currently contains two practical toolbar groups:

| Group | Existing content | Mobile treatment |
|---|---|---|
| Immediate controls | Busy/waiting indicator, interrupt, stop audio, regenerate, nuke regenerate, abort command state | Keep visible near the message input whenever possible. These are core chat controls. |
| Action tools | Actor, narrator, director, time, world, creative, visualizer, and save menus | Keep available, but allow wrapping or a compact overflow pattern if the full row is too wide. |

The MVP should first try to reuse `SceneTools` as-is with mobile CSS adjustments. If the toolbar remains too dense, add a shell-level compact presentation that still delegates actions to the existing `SceneTools*` child components.

Do not split toolbar behavior into a separate mobile implementation. If extraction is needed, split presentation boundaries only. For example, `SceneTools` could expose the immediate controls and action tools as internal sections that both desktop and mobile layouts reuse.

Expected toolbar behavior:

- Interrupt and abort must remain easy to reach during generation or command input.
- Regenerate and directed-regenerate behavior must remain unchanged.
- Audio stop should remain visible when TTS is active.
- Larger action menus may move behind a compact tools button or collapsible row in a later polish pass.
- Toolbar changes must not alter websocket payloads sent by the existing `SceneTools*` components.

### Right End Panels

Mobile should support only two right-end panel modes in the MVP:

| Panel | Existing components | Purpose |
|---|---|---|
| Characters | `CharacterPanel` | Activate and deactivate scene characters. |
| Agents | `AIClient`, `AIAgent` | View and adjust client and agent configuration. |

The panels should be mutually exclusive and opened from top navigation. On narrow screens they should behave like a temporary drawer or full-height overlay, not as fixed-width desktop drawers that squeeze the chat area.

The first implementation can keep the existing drawer components, but the mobile width should be responsive:

- `width="100%"`
- or `:width="mobileLimitedMode ? '100%' : 400"`
- or a Vuetify temporary drawer/dialog pattern if that produces cleaner focus and close behavior.

The important constraint is that `CharacterPanel`, `AIClient`, and `AIAgent` should remain the source components. Avoid creating mobile-specific copies of their logic.

## Top Navigation

In mobile mode, replace the desktop tab-heavy app bar with a compact control surface.

Suggested top navigation:

| Control | Behavior |
|---|---|
| Scene title or compact scene label | Shows the active scene context. |
| Chat button | Closes right panels and returns to chat. |
| Characters button | Opens the character panel. |
| Agents button | Opens the agent panel. |
| Status indicator | Shows connected/disconnected and busy status in compact form. |

Avoid exposing desktop-only destinations in the mobile MVP. World Editor, Mods, Templates, Prompts, Debug, and node-editor controls should be hidden or disabled in mobile mode unless they are explicitly added to a later phase.

## Implementation Shape

Implement this as a responsive shell around existing components, not as a second app.

Recommended state additions in `TalemateApp.vue`:

- `mobileLimitedMode`: true when the display is below the chosen breakpoint.
- `mobilePanel`: one of `null`, `"characters"`, or `"agents"`.
- `isMobilePanelOpen`: derived from `mobilePanel !== null`.

Recommended rendering rules:

1. Keep the existing desktop layout as the default path.
2. In mobile mode, hide the desktop left drawer.
3. In mobile mode, hide desktop top tabs that lead to unsupported views.
4. In mobile mode, render the same chat component stack used by the desktop scene tab.
5. In mobile mode, mount one right panel host and switch the content between `CharacterPanel` and the existing agent settings content.
6. Keep websocket message handling, scene status handling, busy state, and input handling in `TalemateApp.vue`.
7. Keep `SceneTools` in the chat stack, but allow mobile-specific wrapping, density, or section layout around its existing child components.

The agent panel content should be factored only if needed. If reuse inside both desktop and mobile drawers is awkward, extract the current drawer body into a small wrapper component such as `AgentPanel.vue`:

```vue
<template>
  <v-list>
    <AIClient ... />
    <v-divider />
    <v-list-subheader class="text-uppercase">
      <v-icon>mdi-transit-connection-variant</v-icon>
      Agents
    </v-list-subheader>
    <AIAgent ... />
  </v-list>
</template>
```

That wrapper should receive the same props and emit the same events as the current inline content. It should not own websocket state or reimplement save behavior.

## Component Preservation Rules

- Reuse `SceneMessages` for message rendering.
- Reuse `SceneMessageInput` for input behavior and keyboard/prefix handling.
- Reuse `SceneTools` for chat toolbar and action controls.
- Reuse `CharacterPanel` for character activation state.
- Reuse `AIClient` and `AIAgent` for client/agent configuration.
- Keep `TalemateApp.vue` as the coordinator for websocket state, scene state, and drawer/panel selection.
- Extract shared wrapper components only to avoid template duplication.

## Suggested Milestones

### Milestone 1: Mobile Access and Detection

- Confirm `start.bat` keeps backend and frontend on `0.0.0.0`.
- Add `mobileLimitedMode` using Vuetify display state or a narrow viewport listener.
- Verify phone access through `http://<server-name-or-lan-ip>:8082`.

### Milestone 2: Chat-Only Mobile Shell

- Hide desktop navigation and left drawer in mobile mode.
- Render the existing scene chat stack full width.
- Keep the `SceneTools` immediate controls visible and make the action tools wrap or collapse without duplicating action logic.
- Keep desktop behavior unchanged outside mobile mode.

### Milestone 3: Character Panel

- Add top-nav button for Characters.
- Open `CharacterPanel` in a mobile-width temporary right panel.
- Verify activation and deactivation refresh the same way as desktop.

### Milestone 4: Agent Panel

- Add top-nav button for Agents.
- Reuse the existing `AIClient` and `AIAgent` content in the mobile panel.
- Extract a shared `AgentPanel` wrapper only if it removes duplicated template wiring.

### Milestone 5: Polish and Validation

- Check overflow at common mobile widths: `390x844`, `412x915`, and `430x932`.
- Check tablet/narrow desktop width around `768px`.
- Verify the frontend build.
- Verify the desktop shell is unchanged at normal desktop widths.

## Validation

Run the frontend build after implementation:

```powershell
Set-Location talemate_frontend
..\embedded_node\npm.cmd run build
```

Manual browser checks:

- Desktop width: existing tabs, drawers, world editor, prompts, and settings still work.
- Phone portrait: chat view loads without horizontal overflow.
- Phone portrait: interrupt, regenerate, abort, and stop-audio controls remain reachable when applicable.
- Phone portrait: message input can send and skip turns.
- Phone portrait: character panel opens, closes, activates, and deactivates characters.
- Phone portrait: agent panel opens, closes, and can save agent/client settings.
- Phone landscape: top navigation does not overlap status or scene controls.
- LAN device: phone can reach the frontend and websocket using the server hostname or LAN IP.

## Open Decisions

- Breakpoint: use Vuetify's mobile breakpoint or define an explicit Talemate breakpoint such as `smAndDown`.
- Panel presentation: temporary right drawer versus full-screen dialog.
- Chat toolbar density: keep all `SceneTools` actions visible with wrapping, or add a later compact tool menu while keeping immediate controls visible.
- Agent panel scope: include both clients and agents in MVP, or only agent enablement/client assignment.
