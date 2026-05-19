/**
 * Mixin that owns the SceneMessageInput autocompletion flow.
 *
 * Host component requirements:
 * - `messageInput` data property (v-model on SceneMessageInput)
 * - `inputDisabled` data property (disabled while a suggestion is in flight)
 * - `websocket` data property (used to send the autocomplete request)
 * - `getAgents()` method (used to read the creator agent's hints toggle)
 *
 * The host must also forward `autocomplete_suggestion` websocket messages to
 * `handleAutocompleteMessage(data)`, which returns `true` when consumed.
 */

import { primaryModifierLabel } from '@/utils/keyboardModifiers';
import { applyCompletion as applyAutocompleteCompletion } from '@/utils/autocompleteHint';

export default {
    data() {
        return {
            autocompleting: false,
            autocompleteCallback: null,
            autocompleteFocusElement: null,
            autocompleteHintsEnabledAtSend: true,
        }
    },
    methods: {
        handleAutocompleteMessage(data) {
            if (data.type !== 'autocomplete_suggestion') {
                return false;
            }

            if (!this.autocompleteCallback) {
                return true;
            }

            const completion = data.message;
            this.autocompleteCallback(completion);

            if (this.autocompleteFocusElement) {
                const focus_element = this.autocompleteFocusElement;
                setTimeout(() => {
                    focus_element.focus();
                }, 1000);
                this.autocompleteFocusElement = null;
            }

            this.autocompleteCallback = null;
            return true;
        },

        onAutocompleteStart() {
            this.autocompleting = true;
            this.inputDisabled = true;
        },

        onAutocompleteEnd(completion) {
            this.inputDisabled = false;
            this.autocompleting = false;
            // Use the toggle value captured at send time, so toggling mid-flight
            // doesn't desync from what the backend already decided.
            this.messageInput = applyAutocompleteCompletion(
                this.messageInput, completion, this.autocompleteHintsEnabledAtSend
            );
        },

        autocompleteHintsEnabled() {
            // `!== false` mirrors the Python `value=True` default during the
            // brief pre-sync window before agent state has been received.
            const creator = this.getAgents().find(a => a.name === 'creator');
            const value = creator?.actions?.autocomplete?.config?.hints_enabled?.value;
            return value !== false;
        },

        autocompleteRequest(param, callback, focus_element, delay = 500) {
            const hintsEnabled = this.autocompleteHintsEnabled();
            this.autocompleteCallback = (completion) => {
                setTimeout(() => {
                    callback(completion, { hintsEnabled });
                }, delay);
            };
            this.autocompleteFocusElement = focus_element;
            this.autocompleteHintsEnabledAtSend = hintsEnabled;

            const param_copy = JSON.parse(JSON.stringify(param));
            param_copy.type = "assistant";
            param_copy.action = "autocomplete";

            this.websocket.send(JSON.stringify(param_copy));
        },

        autocompleteInfoMessage(active) {
            return active ? 'Generating ...' : `${primaryModifierLabel}+Enter to autocomplete`;
        },
    },
}
