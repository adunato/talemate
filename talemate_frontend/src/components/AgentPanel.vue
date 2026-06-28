<template>
  <v-list>
    <AIClient
      ref="aiClient"
      :immutable-config="appConfig"
      :app-config="appConfig"
      @save="$emit('save-clients', $event)"
      @error="$emit('error', $event)"
      @clients-updated="$emit('clients-updated', $event)"
      @client-assigned="$emit('client-assigned', $event)"
      @open-app-config="openAppConfig"
    />
    <v-divider />
    <v-list-subheader class="text-uppercase">
      <v-icon>mdi-transit-connection-variant</v-icon>
      Agents
    </v-list-subheader>
    <AIAgent
      ref="aiAgent"
      :agentState="agentState"
      :templates="templates"
      :app-config="appConfig"
      @save="$emit('save-agents', $event)"
      @agents-updated="$emit('agents-updated', $event)"
    />
  </v-list>
</template>

<script>
import AIClient from './AIClient.vue';
import AIAgent from './AIAgent.vue';

export default {
  name: 'AgentPanel',
  components: {
    AIClient,
    AIAgent,
  },
  props: {
    agentState: Object,
    templates: Object,
    appConfig: Object,
  },
  emits: [
    'save-clients',
    'save-agents',
    'clients-updated',
    'client-assigned',
    'agents-updated',
    'open-app-config',
    'error',
  ],
  methods: {
    getClients() {
      return this.$refs.aiClient?.state.clients || [];
    },
    getAgents() {
      return this.$refs.aiAgent?.state.agents || [];
    },
    activeClientName() {
      const client = this.$refs.aiClient?.getActive();
      return client ? client.name : null;
    },
    activeAgentName() {
      const agent = this.$refs.aiAgent?.getActive();
      return agent ? agent.label : null;
    },
    configurationRequired() {
      return this.$refs.aiAgent?.configurationRequired() || false;
    },
    openClientModal(initialData = null) {
      this.$refs.aiClient?.openModal(initialData);
    },
    openAgentSettings(agentName, section) {
      this.$refs.aiAgent?.openSettings(agentName, section);
    },
    openAgentMessages(agentName) {
      this.$refs.aiAgent?.openMessages(agentName);
    },
    openAppConfig(...args) {
      this.$emit('open-app-config', ...args);
    },
  },
};
</script>
