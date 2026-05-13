/**
 * Per-message revision-stack state and behavior for the chat view.
 *
 * Each message that supports revisions carries a `revisions: string[]` of
 * full-text versions and a `revision_index: number` pointing at the active
 * one. The stack lives only in client state; the backend keeps a single
 * canonical text per message, which we update via a `swap_revision` action
 * when the user navigates.
 *
 * Two mechanisms feed the stack:
 *  1. Pending-regen stitching: when the backend emits a `remove_message` with
 *     `reason === "regenerate"`, the host calls `revisionBeginPendingRegen`
 *     to defer the removal. The next AI message that arrives consumes the
 *     pending slot via `revisionAddOrCommit`, inheriting the prior stack.
 *  2. Editor revision: when the backend emits a `message_edited` with
 *     `reason === "revision"`, the host calls `revisionAppendAfterCurrent`
 *     to splice the new revised text in after the current entry. The prior
 *     version stays accessible at its existing stack position.
 *
 * Requirements on the host component:
 *  - data: `messages: SceneMessage[]`
 *  - injects: `getWebsocket`
 */
export default {
    data() {
        return {
            pendingRegenSlot: null,
        };
    },
    methods: {
        // Clears in-memory revision state. The host should call this from
        // its own `clear()` / scene-loading reset paths.
        revisionReset() {
            this.pendingRegenSlot = null;
        },

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

        // Initialize a fresh single-entry stack on a brand-new message.
        revisionSeed(message, fullText) {
            if (!this.revisionSupportedType(message.type)) return;
            message.revisions = [fullText];
            message.revision_index = 0;
        },

        revisionFindSlotIndex(slotId) {
            for (let i = this.messages.length - 1; i >= 0; i--) {
                if (this.messages[i].id === slotId) return i;
            }
            return -1;
        },

        // Mark the most-recent message slot as awaiting a regen replacement.
        // Returns true when the slot was successfully marked; false means
        // the caller should fall through to the normal remove-immediately
        // path.
        revisionBeginPendingRegen(messageId) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return false;
            const slot = this.messages[idx];
            if (!this.revisionSupportedType(slot.type)) return false;
            // Flag the slot so the paginator can show a progress indicator
            // until the replacement message lands and we commit the swap.
            slot.regenerating = true;
            this.pendingRegenSlot = {
                priorId: messageId,
                priorRevisions: slot.revisions ? [...slot.revisions] : [],
                priorIndex: slot.revision_index ?? 0,
            };
            return true;
        },

        // Replace a pending-regen slot in place with the just-arrived AI
        // message, inheriting the prior stack and appending the new text.
        // Returns true if a pending slot was consumed.
        revisionCommitRegen(messageObj, fullText) {
            if (!this.pendingRegenSlot) return false;
            if (!this.revisionSupportedType(messageObj.type)) return false;
            const slot = this.pendingRegenSlot;
            // Resolve the slot's current array position at commit time —
            // other messages may have been removed while we were waiting
            // for the replacement, so the snapshotted index is unreliable.
            const idx = this.revisionFindSlotIndex(slot.priorId);
            if (idx < 0) {
                this.pendingRegenSlot = null;
                return false;
            }
            const revisions = slot.priorRevisions.length > 0 ? [...slot.priorRevisions] : [];
            // Dedupe against the version that was canonical before the
            // regenerate started. Identical text means either a no-op
            // regenerate or a failure-restore re-emitting the original;
            // either way there's nothing useful to add to the stack.
            const priorCanonical = revisions[slot.priorIndex];
            if (fullText === priorCanonical) {
                messageObj.revision_index = slot.priorIndex;
            } else {
                revisions.push(fullText);
                messageObj.revision_index = revisions.length - 1;
            }
            messageObj.revisions = revisions;
            this.messages.splice(idx, 1, messageObj);
            this.pendingRegenSlot = null;
            return true;
        },

        // Single entry point for the host's new-message branches. Either
        // replaces a pending regen slot (migrating its stack) or seeds a
        // fresh stack on a brand-new message at the tail.
        revisionAddOrCommit(messageObj, fullText) {
            if (!this.revisionCommitRegen(messageObj, fullText)) {
                this.revisionSeed(messageObj, fullText);
                this.messages.push(messageObj);
            }
        },

        // Editor revision tagged the edit with `reason="revision"`. The
        // prior version is already in the stack at the active index, so we
        // just splice the new (revised) text in after it and advance the
        // pointer onto the new entry.
        revisionAppendAfterCurrent(messageId, fullText) {
            const idx = this.revisionFindSlotIndex(messageId);
            if (idx < 0) return;
            const msg = this.messages[idx];
            if (!this.revisionSupportedType(msg.type)) return;
            if (!msg.revisions || msg.revisions.length === 0) return;
            const cur = msg.revision_index ?? 0;
            msg.revisions.splice(cur + 1, 0, fullText);
            msg.revision_index = cur + 1;
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
