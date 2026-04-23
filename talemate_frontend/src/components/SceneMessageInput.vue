<template>
  <v-textarea
    v-model="inputValue"
    :label="inputHint"
    rows="1"
    auto-grow
    outlined
    ref="textarea"
    @keydown.enter.prevent="onEnter"
    @keydown.ctrl.up.prevent="onHistoryUp"
    @keydown.meta.up.prevent="onHistoryUp"
    @keydown.ctrl.down.prevent="onHistoryDown"
    @keydown.meta.down.prevent="onHistoryDown"
    @keydown.tab.prevent="cycleActAs"
    :hint="inputLongHint"
    :disabled="disabled"
    :loading="autocompleting"
    :prepend-inner-icon="inputIcon"
    :color="inputColor">
    <template v-slot:prepend v-if="sceneActive && sceneEnvironment !== 'creative'">
      <v-btn @click="triggerAutocomplete" color="primary" icon variant="tonal" :disabled="!inputValue || disabled">
        <v-icon>mdi-auto-fix</v-icon>
      </v-btn>
    </template>
    <template v-slot:append>
      <v-btn @click="onEnter" color="primary" icon variant="tonal" :disabled="disabled">
        <v-icon v-if="inputValue">mdi-send</v-icon>
        <v-icon v-else>mdi-skip-next</v-icon>
      </v-btn>
    </template>
  </v-textarea>
</template>

<script>
import { isPrimaryModifier, primaryModifierLabel } from '@/utils/keyboardModifiers';

const INPUT_HISTORY_MAX = 10;

export default {
  name: 'SceneMessageInput',
  inject: ['autocompleteRequest'],
  props: {
    // The textarea's text, bound via v-model on the parent.
    modelValue: { type: String, default: '' },
    // Who the player is speaking as. null = the player character, '$narrator' =
    // the narrator, any other string = a named character. Two-way bound via
    // v-model:act-as; Tab cycling mutates it through update:actAs.
    actAs: { type: String, default: null },
    // App-level activity flags. The component merges them into a single
    // `disabled` computed and doesn't react to them individually.
    busy: { type: Boolean, default: false },
    ready: { type: Boolean, default: false },
    uxInteractionActive: { type: Boolean, default: false },
    // Parent-owned "we've already sent, waiting on the server" gate. The
    // parent flips this to true right after our `send` / `autocomplete-start`
    // and back to false when the server responds. Prevents double-sends.
    inputDisabled: { type: Boolean, default: false },
    // Parent-owned "server is asking the user for input right now" state.
    // Drives all hint/icon/color branches — when false, the input is an idle
    // skip-turn control (mdi-cancel) and has no label or color.
    waitingForInput: { type: Boolean, default: false },
    // Payload of the current request_input message from the server. `.reason`
    // === 'talk' means dialogue prompt (player/character/narrator line);
    // anything else means a generic prompt and `.message` is shown as label.
    inputRequestInfo: { type: Object, default: null },
    // Autocomplete is in-flight — drives the textarea's :loading state.
    autocompleting: { type: Boolean, default: false },
    // A scene is loaded. Gates the autocomplete prepend button visibility.
    sceneActive: { type: Boolean, default: false },
    // 'scene' vs 'creative' — autocomplete prepend is hidden in creative mode.
    sceneEnvironment: { type: String, default: 'scene' },
    // Map of character name -> color string; used to tint the input based on
    // who the user is currently speaking as.
    characterColors: { type: Object, default: () => ({}) },
    // Fallback for the hint label when actAs is null (player talks as self).
    playerCharacterName: { type: String, default: null },
    // Needed by cycleActAs (Tab) — the order determines cycle order.
    activeCharacters: { type: Array, default: () => [] },
  },
  emits: [
    'update:modelValue',
    'update:actAs',
    'send',
    'autocomplete-start',
    'autocomplete-end',
  ],
  data() {
    return {
      inputHistory: [],
      historyIndex: 0,
      draftBeforeHistoryBrowse: '',
    };
  },
  computed: {
    // v-model indirection: read from the prop, write through the emit so the
    // parent stays the source of truth for the textarea's value.
    inputValue: {
      get() { return this.modelValue; },
      set(v) { this.$emit('update:modelValue', v); },
    },
    // Single disabled signal for the textarea and both buttons. Combines
    // app-wide gating (busy/ready/uxInteractionActive) with the parent's
    // explicit inputDisabled flag.
    disabled() {
      return this.busy || !this.ready || this.uxInteractionActive || this.inputDisabled;
    },
    // True only when the server is actively soliciting a dialogue line.
    // Autocomplete is only meaningful in that context.
    isWaitingForDialogInput() {
      return this.waitingForInput && this.inputRequestInfo && this.inputRequestInfo.reason === 'talk';
    },
    // The floating textarea label — the speaker name for dialogue prompts,
    // the server's message text for generic prompts, empty when idle.
    inputHint() {
      if (this.waitingForInput) {
        if (this.inputRequestInfo?.reason === 'talk') {
          const characterName = this.actAs ? this.actAs : this.playerCharacterName;
          if (characterName === '$narrator') return 'Narrator:';
          return `${characterName}:`;
        }
        return this.inputRequestInfo?.message;
      }
      return '';
    },
    // Keyboard-hint line shown below the textarea during dialogue prompts.
    // Intentionally hidden for non-talk prompts to keep server messages clean.
    inputLongHint() {
      if (this.waitingForInput && this.inputRequestInfo?.reason === 'talk') {
        return `${primaryModifierLabel}+Enter to autocomplete, Shift+Enter for newline, ${primaryModifierLabel}+Up/Down for history, Tab to act as another character. Start messages with '@' to do an action. (e.g., '@look at the door')`;
      }
      return '';
    },
    // Prepend icon: warning glyph for generic prompts, speaker-type glyph for
    // dialogue prompts, mdi-cancel when idle (signalling "skip turn" behavior).
    inputIcon() {
      if (this.waitingForInput) {
        if (this.inputRequestInfo?.reason !== 'talk') {
          return 'mdi-information-outline';
        }
        if (this.actAs === '$narrator') return 'mdi-script-text-outline';
        return 'mdi-comment-outline';
      }
      return 'mdi-cancel';
    },
    // Textarea color: warning for generic prompts, per-character color for
    // dialogue prompts when colors are configured, primary as fallback, null
    // (inherits default) when idle.
    inputColor() {
      if (!this.waitingForInput) return null;
      if (this.inputRequestInfo?.reason !== 'talk') return 'warning';
      if (!this.characterColors || !this.characterColors[this.playerCharacterName]) {
        return 'primary';
      }
      if (this.actAs) {
        if (this.actAs === '$narrator') return 'narrator';
        return this.characterColors[this.actAs];
      }
      return this.characterColors[this.playerCharacterName];
    },
  },
  methods: {
    focus() {
      this.$refs.textarea?.focus();
    },
    scrollIntoView(options) {
      this.$el?.scrollIntoView?.(options);
    },
    triggerAutocomplete() {
      if (!this.isWaitingForDialogInput) return;

      this.$emit('autocomplete-start');

      let context = 'dialogue:player';
      if (this.actAs) {
        context = this.actAs === '$narrator' ? 'narrative:' : `dialogue:${this.actAs}`;
      }

      // `this` is passed as the focus target — the parent's autocompleteRequest
      // calls .focus() on it after the suggestion arrives, which hits our
      // exposed focus() method and delegates to the inner textarea.
      this.autocompleteRequest(
        {
          partial: this.inputValue,
          context,
          character: this.actAs,
        },
        (completion) => {
          this.$emit('autocomplete-end', completion);
        },
        this,
        100,
      );
    },
    onEnter(event) {
      if (event && isPrimaryModifier(event) && event.key === 'Enter') {
        return this.triggerAutocomplete();
      }
      if (this.uxInteractionActive) return;
      if (event && event.shiftKey && event.key === 'Enter') {
        const textarea = this.$refs.textarea.$el.querySelector('textarea');
        const cursorPos = textarea.selectionStart;
        this.inputValue = this.inputValue.slice(0, cursorPos) + '\n' + this.inputValue.slice(cursorPos);
        this.$nextTick(() => {
          textarea.selectionStart = textarea.selectionEnd = cursorPos + 1;
        });
        return;
      }
      if (!this.inputDisabled) {
        const sentText = this.inputValue;
        this.$emit('send', { text: sentText, actAs: this.actAs });
        const trimmed = (sentText || '').trim();
        if (trimmed.length > 0) {
          this.inputHistory.unshift(sentText);
          if (this.inputHistory.length > INPUT_HISTORY_MAX) {
            this.inputHistory.length = INPUT_HISTORY_MAX;
          }
        }
        this.draftBeforeHistoryBrowse = '';
        this.historyIndex = 0;
        this.inputValue = '';
      }
    },
    onHistoryUp() {
      if (!this.inputHistory || this.inputHistory.length === 0) return;
      const maxUp = this.inputHistory.length;
      if (this.historyIndex <= -maxUp) return;
      if (this.historyIndex === 0) {
        this.draftBeforeHistoryBrowse = this.inputValue;
      }
      this.historyIndex -= 1;
      const historyPos = -this.historyIndex - 1;
      this.inputValue = this.inputHistory[historyPos] ?? '';
      this.moveCursorToEnd();
    },
    onHistoryDown() {
      if (this.historyIndex === 0) return;
      this.historyIndex += 1;
      if (this.historyIndex === 0) {
        this.inputValue = this.draftBeforeHistoryBrowse || '';
        this.moveCursorToEnd();
        return;
      }
      const historyPos = -this.historyIndex - 1;
      if (historyPos < 0 || historyPos >= this.inputHistory.length) {
        this.historyIndex = 0;
        return;
      }
      this.inputValue = this.inputHistory[historyPos] ?? '';
      this.moveCursorToEnd();
    },
    moveCursorToEnd() {
      this.$nextTick(() => {
        const textarea = this.$refs.textarea?.$el?.querySelector('textarea');
        if (textarea) {
          const len = this.inputValue.length;
          textarea.selectionStart = textarea.selectionEnd = len;
        }
      });
    },
    cycleActAs() {
      const playerCharacterName = this.playerCharacterName;

      if (!this.activeCharacters || this.activeCharacters.length === 0) {
        this.$emit('update:actAs', '$narrator');
        return;
      }

      if (this.actAs === '$narrator') {
        this.$emit('update:actAs', null);
        return;
      }

      let selectedCharacter = null;
      let foundActAs = false;

      for (const characterName of this.activeCharacters) {
        if (this.actAs === '$narrator') {
          selectedCharacter = characterName;
          break;
        }
        if (this.actAs === null && characterName !== playerCharacterName) {
          selectedCharacter = characterName;
          break;
        }
        if (foundActAs) {
          selectedCharacter = characterName;
          break;
        } else if (characterName === this.actAs) {
          foundActAs = true;
        }
      }

      if (selectedCharacter === null || selectedCharacter === playerCharacterName) {
        this.$emit('update:actAs', '$narrator');
      } else {
        this.$emit('update:actAs', selectedCharacter);
      }
    },
  },
};
</script>
