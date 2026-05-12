# Context DB

A read-only interface to browse and search the current context database managed by the [Memory Agent](/talemate/user-guide/agents/memory/).

!!! note
    This interface will likely be revamped soon, so documentation will be minimal currently.

## Searching

Search is done by typing in the search field and pressing `Enter`.

The search looks for the entered text based on relevancy using embeddings. Without getting too technical here, that means if you're using the basic chromadb configuration, accuracy may be lacking.

See [Memory Agent - Embeddings](/talemate/user-guide/agents/memory/embeddings) for more information on how to improve the search accuracy.

![world editor history](/talemate/img/0.26.0/world-editor-history.png)

### Search Strictness

The **Search Strictness** slider tunes how closely a result must match the query before it is returned.

![Context Database search strictness slider](/talemate/img/0.37.0/context-db-search-strictness-slider.png)

- Range: `0.1` to `2.0`, in steps of `0.1`.
- Default: `1.0`.
- **Lower values** require closer matches (stricter search, fewer but more relevant results).
- **Higher values** accept more loosely related results (looser search, more results of lower relevance).

The slider value is the `distance_mod` multiplier on the active embedding preset's `distance` setting, and it is applied immediately to any subsequent search.

!!! info "Saved to the active embedding preset"
    Moving the slider writes the new value to the embedding preset currently selected in the [Memory agent settings](/talemate/user-guide/agents/memory/settings). The change persists across restarts and affects every search that uses that preset.

    The same value is also editable from the embedding preset itself as the [Distance Mod](/talemate/user-guide/agents/memory/embeddings/#distance-mod) field.

## Adding an entry

While you can manually add an entry through this interface, its not really encouraged anymore.

It is better to use the :material-earth: **World** and :material-account-group: **Characters** tabs to add entries to the context database.

## Tools

### Reset

Resets the context database, and will remove all entries and then re-populate it with the entries in the current scene.

!!! warning
    Entries added manually directly to the context db will not be in the scene file, and be lost during this operation.
