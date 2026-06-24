# Configurable Background Agent Updates

## Assessment

Talemate can support non-blocking maintenance updates with a relatively small scheduling change:

- Each agent may run at most one background task.
- If that agent receives another request while its background task is running, the new request waits for the task to finish and then runs normally.
- Different agents may work at the same time.
- Conversation and scene-content generation remain asynchronous and serial.

This is substantially simpler than supporting multiple concurrent tasks within an agent. It avoids same-agent write races, duplicated agent activity, competing requests through the same client assignment, and the need to display several simultaneous actions for one agent.

## Current behavior

Talemate's async signal dispatcher awaits listeners one at a time. This causes automatic maintenance work to extend the foreground turn:

- Summarisation is awaited after scene history changes.
- World-state extraction, reinforcement updates, and conditional-pin checks are awaited during the game loop.
- Character progression is also awaited during the game loop.

The codebase already distinguishes normal agent work from background work:

- `busy` work blocks scene interaction.
- `busy_bg` work remains visible without applying the global UI lock.
- The activity bar already displays background agent activity in a different color.
- TTS and visual generation already use background processing.

The missing piece is a consistent one-task-per-agent scheduler.

## Proposed scheduling rule

Each agent receives a single background-task slot and an agent-level lock.

When an activity is configured as **Background**:

1. If the agent is idle, schedule the activity and return control to the scene.
2. Mark the agent as `busy_bg`.
3. While the task runs, any new call to that agent waits on the agent lock.
4. When the background task finishes, waiting calls continue in arrival order.

When an activity is configured as **Blocking**, it behaves as it does today.

This means background work is non-blocking for the scene until another part of the scene actually needs the same agent. For example, background archive summarisation may continue while the player reads or types, but conversation generation will wait if it requires scene analysis from the summarizer before the archive task has completed.

## Data integrity

The one-task-per-agent rule prevents concurrent operations inside the same agent. Talemate runs these tasks on one asyncio event loop, so ordinary list and dictionary updates are not being mutated by multiple threads.

A delayed result may be based on slightly older scene content. That is acceptable for this feature. Talemate does not need scene revisions, snapshot hashes, stale-result rejection, or automatic recomputation.

Only protections against actual invalid writes are required:

- A task must retain the scene instance it started with and must never write through an agent that has since been connected to another scene.
- Active tasks must be cancelled when their scene is unloaded, reset, or restored.
- Before updating a specifically addressed object, such as a reinforcement or character, confirm that the object still exists. If it was deleted while generation was running, drop the result rather than recreate it accidentally.
- The agent lock must always be released after success, cancellation, or failure.

## Configurable background activities

| Agent | Activity | Background suitability | Notes |
|---|---|---:|---|
| Summarizer | Base archive summarisation | High | The summarizer's single task preserves archive order. |
| Summarizer | Layered-history summarisation | High | Runs after base archive summarisation within the same agent task. |
| Summarizer | Scene-analysis pre-generation | Medium | Only useful as cached preparation for a future turn. Analysis required by the current generation waits normally. |
| World State | World-state extraction | High | A delayed update may reflect slightly older scene content. |
| World State | State reinforcement updates | High | Process all due reinforcements serially within one background agent task. |
| World State | Conditional context-pin checks | High | Can follow reinforcements in the same task. |
| World State | Character-progression assessment | Medium | Safe by default when producing suggestions. |
| Director | Player-choice suggestions | Medium | Delayed choices may be less relevant, but this is a UX issue rather than a data-integrity issue. |
| TTS | Speech generation | Already background | Keep the existing serial queue. |
| Visual | Image generation and analysis | Already background | The visual agent may retain its existing backend queue if treated as its single agent task. |

Memory indexing triggered by a completed archive update can remain part of the summarizer task even though the write is performed by the memory agent. Alternatively, it can be handed to the memory agent as its own single background task. The second approach avoids keeping the summarizer busy during embedding work.

## Activities that must remain serial

The following must not become detached background tasks:

- Conversation and actor-response generation.
- Narration and automatic narration.
- Scene-direction execution that determines current scene content.
- Editor cleanup or revision that changes content before it is committed.
- Scene analysis, director guidance, or memory retrieval required by the current generation.
- Current-message history mutation.
- Regeneration and cancellation of current scene content.

These operations remain asynchronous internally, but only one scene-content operation runs at a time.

## Reinforcement delay

Sequential reinforcements append a message to scene history. If one completes late, it is appended when it completes. This may not perfectly represent the turn on which it was triggered, but it does not corrupt scene data.

The only special case is deletion: if the reinforcement no longer exists when generation completes, discard the result.

## User configuration

Add an **Execution** choice to each eligible existing agent action:

- **Blocking** — current behavior.
- **Background** — use the agent's single background slot.

The setting belongs in the existing agent configuration beside the activity's enable and frequency settings. This keeps configuration discoverable and avoids a separate technical scheduler screen.

Recommended defaults:

- Existing automatic activities remain **Blocking** for compatibility.
- TTS and visual activities retain their current background behavior.
- Users opt individual activities into **Background**.

No user-facing parallelism count is needed. The rule is always one active task per agent.

## UI visualization

The existing activity bar can represent this model with minimal changes:

- One chip per busy agent.
- Primary color for blocking work.
- Secondary color for background work.
- Display the current activity, such as `Summarizer · Updating archive`.
- If another request is waiting for that agent, show a small `1 waiting` indicator.
- Allow cancellation where the underlying operation supports it.

The current `_current_action` field is sufficient because an agent can only perform one task at a time. The frontend does not need a general multi-task registry.

## Side impacts

### Foreground requests may wait

A foreground request can encounter an agent already performing background work. Waiting is safe and predictable, but it may cause a delayed response after the user submits an action.

The UI should change the activity label from background-only status to something explicit, such as:

`Summarizer · Updating archive · Conversation waiting`

Long-running background tasks should support cancellation or foreground priority. A practical policy is to let a foreground request cancel a cancellable background task, or wait when cancellation would waste too much completed work.

### Different agents still run concurrently

The summarizer and world-state agent may update different parts of the scene at the same time. Since execution is on one asyncio event loop, their synchronous mutation sections do not run simultaneously.

No general scene-level commit lock is required. If a specific operation later proves to contain an unsafe mutation across an `await`, that operation can be corrected locally.

### Shared AI clients

Two different agents may use the same AI client simultaneously. Some local backends may not handle that well, and concurrent maintenance work may delay conversation generation.

The client layer should serialize requests when the backend does not support concurrency. Foreground generation should receive priority over queued background calls.

### Save and unload

Auto-save should include only completed updates. On scene unload, reset, or restore:

- Cancel active background tasks.
- Clear waiting agent calls.
- Reject any late result from the previous scene instance.

### Errors

A failed background task must:

- Release the agent lock.
- Clear `busy_bg`.
- Leave current scene state unchanged.
- Notify the user without locking scene interaction.

## Implementation outline

1. Add an agent-level operation lock and one tracked background task to the base agent.
2. Make normal agent calls wait for the tracked task before starting.
3. Add the `Blocking` / `Background` execution setting to eligible actions.
4. Capture the originating scene reference and cancel tasks during scene replacement.
5. Drop results whose explicitly targeted object was deleted.
6. Extend the activity bar with a waiting indicator.
7. Convert activities incrementally, starting with summarisation and world-state maintenance.

## Conclusion

One background task per agent is a practical design and considerably reduces implementation risk.

It preserves a clear rule:

- Work inside an agent is serial.
- Different agents may work concurrently.
- Scene-content generation is always serial.
- A foreground request waits when it needs an agent that is still completing background work.

Delayed but valid results are accepted. Additional consistency machinery should only be added if implementation testing identifies a concrete data-corruption path.
