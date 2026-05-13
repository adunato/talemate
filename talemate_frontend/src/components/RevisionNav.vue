<template>
  <span v-if="count > 1 || busy" class="revision-nav" :title="title">
    <template v-if="count > 1">
      <v-btn size="x-small" icon variant="text" density="compact" class="revision-arrow" :disabled="index <= 0 || disabled || busy" @click.stop="$emit('navigate', -1)">
        <v-icon size="small">mdi-chevron-left</v-icon>
      </v-btn>
      <span class="revision-counter">{{ index + 1 }}/{{ count }}</span>
      <v-btn size="x-small" icon variant="text" density="compact" class="revision-arrow" :disabled="index >= count - 1 || disabled || busy" @click.stop="$emit('navigate', 1)">
        <v-icon size="small">mdi-chevron-right</v-icon>
      </v-btn>
    </template>
    <v-chip v-if="busy" size="small" variant="text" class="revision-busy-chip" color="primary">
      <v-progress-circular indeterminate size="16" width="2" color="primary" class="mr-2" />
      Regenerating
    </v-chip>
  </span>
</template>

<script>
export default {
  name: 'RevisionNav',
  props: {
    count: {
      type: Number,
      required: true,
    },
    index: {
      type: Number,
      required: true,
    },
    disabled: {
      type: Boolean,
      default: false,
    },
    busy: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['navigate'],
  computed: {
    title() {
      if (this.busy) return 'Regenerating…';
      return `Revision ${this.index + 1} of ${this.count}`;
    },
  },
}
</script>

<style scoped>
.revision-nav {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  margin-top: 2px;
  color: rgb(var(--v-theme-muted));
  font-size: 0.75rem;
  user-select: none;
}

.revision-nav :deep(.revision-busy-chip.v-chip) {
  height: 24px;
}

.revision-nav .revision-counter {
  margin: 0 2px;
  min-width: 28px;
  text-align: center;
  opacity: 0.7;
}

.revision-nav .revision-arrow {
  width: 18px;
  height: 18px;
  opacity: 0.7;
}

.revision-busy-chip {
  margin-left: 4px;
}
</style>
