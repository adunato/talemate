<template>
  <DynamicAgentRegistry
    :children="children"
    add-label="Add backend"
    add-placeholder="Backend name (e.g. vLLM Local)"
    :description="description"
    @add="(label) => $emit('add', label)"
    @remove="(slug) => $emit('remove', slug)"
    @rename="(slug, label) => $emit('rename', [slug, label])"
  >
    <template #child-actions="{ child }">
      <v-btn
        size="small"
        prepend-icon="mdi-refresh"
        variant="tonal"
        color="secondary"
        class="mr-2"
        @click="$emit('refresh-voices', child.slug)"
        title="Pull the voice list from the backend"
      >Refresh voices</v-btn>
    </template>
  </DynamicAgentRegistry>
</template>

<script>
import DynamicAgentRegistry from './DynamicAgentRegistry.vue';

export default {
  name: 'TTSOpenAICompatibleBackends',
  components: { DynamicAgentRegistry },
  props: {
    children: { type: Array, required: true },
    description: { type: String, default: '' },
  },
  emits: ['add', 'remove', 'rename', 'refresh-voices'],
};
</script>
