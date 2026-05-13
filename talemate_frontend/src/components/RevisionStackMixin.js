/**
 * Per-message revision-stack state and behavior for the chat view.
 *
 * Each message that supports revisions carries a `revisions: string[]` of
 * full-text versions and a `revision_index: number` pointing at the active
 * one. The stack lives only in client state; the backend keeps a single
 * canonical text per message, which we update via a `swap_revision` action
 * when the user navigates.
 *
 * Three mechanisms feed the stack:
 *  1. Auto-revision on a brand-new generation: the editor's push-time
 *     hook `revision_on_push` rewrites character/narrator messages when
 *     they're pushed to scene history, and the backend's
 *     character/narrator wire payload carries `mutations: string[]` of
 *     prior versions. `revisionSeed` lays them down ahead of the
 *     canonical text in the fresh stack.
 *  2. Manual editor revision: when the backend emits a `message_edited`
 *     with `reason === "revision"`, the host calls
 *     `revisionAppendAfterCurrent` — the new entry is a revision *of*
 *     the active entry and slots in directly after it. Prior tail
 *     entries shift down.
 *  3. In-place regenerate: when the backend emits a `message_edited`
 *     with `reason === "regenerate"`, the host calls
 *     `revisionAppendAtEnd` with `[...mutations, new_canonical]`. The
 *     result is a fresh alternative for the slot rather than a revision
 *     of the active entry, so it appends to the end of the stack and
 *     leaves prior entries at their original positions.
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

        // Initialize the stack on a brand-new message. When automated
        // mutators (today: editor auto-revision) overwrote earlier
        // versions of the canonical text during generation, the backend
        // ships each captured original in `mutations`; seed them ahead of
        // the current text so the user can arrow back through them.
        revisionSeed(message, fullText, mutations = []) {
            if (!this.revisionSupportedType(message.type)) return;
            const prior = (mutations || []).filter(m => m && m !== fullText);
            if (prior.length > 0) {
                message.revisions = [...prior, fullText];
                message.revision_index = prior.length;
            } else {
                message.revisions = [fullText];
                message.revision_index = 0;
            }
        },

        revisionFindSlotIndex(slotId) {
            for (let i = this.messages.length - 1; i >= 0; i--) {
                if (this.messages[i].id === slotId) return i;
            }
            return -1;
        },

        // Single entry point for the host's new-message branches. Seeds a
        // fresh stack on a brand-new message at the tail. `mutations` is
        // the list of prior versions of the canonical text shipped by the
        // backend when automated mutators (today: editor auto-revision)
        // overwrote the message during generation.
        revisionAddOrCommit(messageObj, fullText, mutations = []) {
            this.revisionSeed(messageObj, fullText, mutations);
            this.messages.push(messageObj);
        },

        // Splice one or more new entries onto the stack after the
        // currently-active one and advance the pointer to the last of
        // them. Used by manual editor revision: the new entry is a
        // revision *of* the active entry, so it slots in directly after
        // it and the prior tail entries shift down.
        revisionAppendAfterCurrent(messageId, textOrItems) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return;
            const msg = this.messages[idx];
            if (!this.revisionSupportedType(msg.type)) return;
            if (!msg.revisions || msg.revisions.length === 0) return;
            const items = Array.isArray(textOrItems) ? textOrItems : [textOrItems];
            if (items.length === 0) return;
            const cur = msg.revision_index ?? 0;
            msg.revisions.splice(cur + 1, 0, ...items);
            msg.revision_index = cur + items.length;
        },

        // Append one or more new entries onto the end of the stack and
        // point at the last of them. Used by in-place regenerate: the
        // new text is a fresh alternative for the slot, not a revision
        // of the active entry — putting it at the end keeps prior
        // entries in their original positions. `textOrItems` is either a
        // single string or an array (intermediate mutations followed by
        // the new canonical text).
        revisionAppendAtEnd(messageId, textOrItems) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return;
            const msg = this.messages[idx];
            if (!this.revisionSupportedType(msg.type)) return;
            if (!msg.revisions || msg.revisions.length === 0) return;
            const items = Array.isArray(textOrItems) ? textOrItems : [textOrItems];
            if (items.length === 0) return;
            msg.revisions.push(...items);
            msg.revision_index = msg.revisions.length - 1;
        },

        // The user manually edited a message body. The edit replaces the
        // current stack entry in place rather than pushing a new revision.
        revisionUpdateCurrentEntry(messageId, fullText) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return;
            const msg = this.messages[idx];
            if (!msg.revisions || msg.revisions.length === 0) return;
            const cur = msg.revision_index ?? 0;
            msg.revisions.splice(cur, 1, fullText);
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
            const fullText = msg.revisions[newIndex];
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
