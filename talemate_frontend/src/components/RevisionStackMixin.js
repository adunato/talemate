/**
 * Per-message revision-stack state and behavior for the chat view.
 *
 * Each message that supports revisions carries a `revisions` list of
 * `{message, source}` entries and a `revision_index` pointing at the
 * active one. `source` is one of `"original"`, `"revision"`, or
 * `"regenerate"` and identifies the origin of *that specific entry* in
 * the stack — the UI uses it to show a per-revision source badge so the
 * user can tell at a glance whether they're looking at the original
 * generation, an editor revision, or a regenerate output.
 *
 * The stack lives only in client state; the backend keeps a single
 * canonical text per message, which we update via a `swap_revision`
 * action when the user navigates.
 *
 * Three mechanisms feed the stack:
 *  1. Auto-revision on a brand-new generation: the editor's push-time
 *     hook `revision_on_push` rewrites character/narrator messages when
 *     they're pushed to scene history. The wire payload carries
 *     `mutations: {message, source}[]` of prior versions plus the
 *     canonical's own `source` field. `revisionSeed` lays the mutations
 *     down ahead of the canonical text in the fresh stack.
 *  2. Manual editor revision: when the backend emits a `message_edited`
 *     with `reason === "revision"`, the host calls
 *     `revisionAppendAfterCurrent` — the new entry is a revision *of*
 *     the active entry and slots in directly after it. Prior tail
 *     entries shift down.
 *  3. In-place regenerate: when the backend emits a `message_edited`
 *     with `reason === "regenerate"`, the host calls
 *     `revisionAppendAtEnd` with `[...mutations, {message, source}]`.
 *     The result is a fresh alternative for the slot rather than a
 *     revision of the active entry, so it appends to the end of the
 *     stack and leaves prior entries at their original positions.
 *
 * Requirements on the host component:
 *  - data: `messages: SceneMessage[]`
 *  - injects: `getWebsocket`
 */
export default {
    methods: {
        // Character messages carry their canonical text as "Name: body" but
        // the renderer shows just the body. Strip the prefix for display;
        // other supported types are body-only already.
        revisionStripForDisplay(type, character, fullText) {
            if (type === 'character' && character) {
                const parts = (fullText || '').split(':');
                if (parts.length > 1) {
                    parts.shift();
                    return parts.join(':').trim();
                }
            }
            return (fullText || '').trim();
        },

        // Only these message types can be regenerated/revised; everything
        // else never grows a stack.
        revisionSupportedType(type) {
            return type === 'character' || type === 'narrator' || type === 'context_investigation';
        },

        revisionCurrentSource(message) {
            if (!message || !message.revisions || message.revisions.length === 0) return null;
            const entry = message.revisions[message.revision_index ?? 0];
            return entry ? entry.source : null;
        },

        // Initialize the stack on a brand-new message. When automated
        // mutators (today: editor auto-revision) overwrote earlier
        // versions of the canonical text during generation, the backend
        // ships each captured original in `mutations`; seed them ahead of
        // the current text so the user can arrow back through them.
        revisionSeed(message, fullText, mutations = [], canonicalSource = 'original') {
            if (!this.revisionSupportedType(message.type)) return;
            const prior = (mutations || []).filter(m => m && m.message && m.message !== fullText);
            const canonical = { message: fullText, source: canonicalSource };
            if (prior.length > 0) {
                message.revisions = [...prior, canonical];
                message.revision_index = prior.length;
            } else {
                message.revisions = [canonical];
                message.revision_index = 0;
            }
        },

        revisionFindSlotIndex(slotId) {
            for (let i = this.messages.length - 1; i >= 0; i--) {
                if (this.messages[i].id === slotId) return i;
            }
            return -1;
        },

        // Single entry point for the host's new-message branches. Seeds
        // a fresh stack on a brand-new message at the tail.
        revisionAddOrCommit(messageObj, fullText, mutations = [], canonicalSource = 'original') {
            this.revisionSeed(messageObj, fullText, mutations, canonicalSource);
            this.messages.push(messageObj);
        },

        // Splice one or more new entries onto the stack after the
        // currently-active one and advance the pointer to the last of
        // them. Used by manual editor revision: the new entry is a
        // revision *of* the active entry, so it slots in directly after
        // it and the prior tail entries shift down.
        revisionAppendAfterCurrent(messageId, items) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return;
            const msg = this.messages[idx];
            if (!this.revisionSupportedType(msg.type)) return;
            if (!msg.revisions || msg.revisions.length === 0) return;
            if (!items || items.length === 0) return;
            const cur = msg.revision_index ?? 0;
            msg.revisions.splice(cur + 1, 0, ...items);
            msg.revision_index = cur + items.length;
        },

        // Append one or more new entries onto the end of the stack and
        // point at the last of them. Used by in-place regenerate: the
        // new text is a fresh alternative for the slot, not a revision
        // of the active entry — putting it at the end keeps prior
        // entries in their original positions.
        revisionAppendAtEnd(messageId, items) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return;
            const msg = this.messages[idx];
            if (!this.revisionSupportedType(msg.type)) return;
            if (!msg.revisions || msg.revisions.length === 0) return;
            if (!items || items.length === 0) return;
            msg.revisions.push(...items);
            msg.revision_index = msg.revisions.length - 1;
        },

        // The user manually edited a message body. The edit replaces
        // the current stack entry's text in place rather than pushing a
        // new revision.
        revisionUpdateCurrentEntry(messageId, fullText) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return;
            const msg = this.messages[idx];
            if (!msg.revisions || msg.revisions.length === 0) return;
            const cur = msg.revision_index ?? 0;
            const entry = msg.revisions[cur];
            msg.revisions.splice(cur, 1, { message: fullText, source: entry ? entry.source : 'original' });
        },

        // Switch the active revision for a message, update its rendered
        // text, and tell the backend to canonicalize the chosen version.
        navigateRevision(messageId, direction) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return;
            const msg = this.messages[idx];
            if (!msg.revisions || msg.revisions.length < 2) return;
            const newIndex = (msg.revision_index ?? 0) + direction;
            if (newIndex < 0 || newIndex >= msg.revisions.length) return;
            msg.revision_index = newIndex;
            const entry = msg.revisions[newIndex];
            const fullText = entry.message;
            msg.text = this.revisionStripForDisplay(msg.type, msg.character, fullText);
            this.getWebsocket().send(JSON.stringify({
                type: 'scene_message',
                action: 'swap_revision',
                id: messageId,
                text: fullText,
            }));
        },
    },
};
