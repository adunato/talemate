<template>
  <!-- editing indicator -->
  <v-chip size="x-small" color="indigo-lighten-4" v-if="editing">
    <v-icon class="mr-1">mdi-pencil</v-icon>
    Editing - Press `enter` to submit. Click anywhere to cancel.
  </v-chip>

  <template v-else>
    <!-- edit hint -->
    <v-chip size="x-small" color="grey-lighten-1" variant="text" class="mr-1">
      <v-icon>mdi-pencil</v-icon>
      Double-click to edit.
    </v-chip>

    <!-- create pin -->
    <v-chip v-if="showPin" size="x-small" class="ml-2" label color="success" variant="outlined" @click="createPin(messageId)" :disabled="uxLocked">
      <v-icon class="mr-1">mdi-pin</v-icon>
      Create Pin
    </v-chip>

    <!-- revision -->
    <v-chip v-if="showRevision && editorRevisionsEnabled && isLastMessage" size="x-small" class="ml-2" label color="dirty" variant="outlined" @click="reviseMessage(messageId)" :disabled="uxLocked">
      <v-icon class="mr-1">mdi-typewriter</v-icon>
      Editor Revision
    </v-chip>

    <!-- fork scene -->
    <v-chip v-if="showFork && forkable" size="x-small" class="ml-2" label :color="rev > 0 ? 'highlight1' : 'muted'" variant="outlined" @click="forkSceneInitiate(messageId)" :disabled="uxLocked">
      <v-icon class="mr-1">mdi-source-fork</v-icon>
      Fork
    </v-chip>

    <!-- type-specific actions (e.g. Continue) -->
    <slot name="extra-actions" />

    <!-- generate tts -->
    <v-chip v-if="showTts && ttsAvailable" size="x-small" class="ml-2" label color="secondary" variant="outlined" @click="generateTTS(messageId)" :disabled="uxLocked || ttsBusy">
      <v-icon class="mr-1">mdi-account-voice</v-icon>
      TTS
      <v-progress-circular v-if="ttsBusy" class="ml-2" size="14" indeterminate="disable-shrink" color="secondary"></v-progress-circular>
    </v-chip>

    <!-- insert time passage -->
    <v-chip v-if="showTimePassage" size="x-small" class="ml-2" label color="time" variant="outlined" @click="insertTimePassage(messageId)" :disabled="uxLocked">
      <v-icon class="mr-1">mdi-clock-plus-outline</v-icon>
      Time Passage
    </v-chip>
  </template>
</template>

<script>
// Shared hover toolbar for scene message components (CharacterMessage,
// NarratorMessage, ContextInvestigationMessage, ...). Action handlers are
// pulled from the provide/inject tree exposed by SceneMessages.vue, so the
// only wiring a consumer needs is state props + which actions to show.
export default {
  name: 'MessageToolbar',
  // Renders a fragment (multiple sibling chips) — consumers must wrap it
  // (e.g. in a <v-sheet>); inheritAttrs is off so stray attrs don't warn.
  inheritAttrs: false,
  props: {
    // id passed to the action handlers
    messageId: {
      type: [String, Number],
      required: true,
    },
    editing: {
      type: Boolean,
      default: false,
    },
    uxLocked: {
      type: Boolean,
      default: false,
    },
    isLastMessage: {
      type: Boolean,
      default: false,
    },
    editorRevisionsEnabled: {
      type: Boolean,
      default: false,
    },
    ttsAvailable: {
      type: Boolean,
      default: false,
    },
    ttsBusy: {
      type: Boolean,
      default: false,
    },
    rev: {
      type: Number,
      default: 0,
    },
    sceneRev: {
      type: Number,
      default: 0,
    },
    // action toggles
    showPin: {
      type: Boolean,
      default: true,
    },
    showRevision: {
      type: Boolean,
      default: true,
    },
    showFork: {
      type: Boolean,
      default: true,
    },
    showTts: {
      type: Boolean,
      default: true,
    },
    showTimePassage: {
      type: Boolean,
      default: true,
    },
  },
  inject: [
    'createPin',
    'reviseMessage',
    'forkSceneInitiate',
    'generateTTS',
    'insertTimePassage',
  ],
  computed: {
    forkable() {
      return this.rev <= this.sceneRev;
    },
  },
}
</script>
