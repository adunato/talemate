<template>
  <v-dialog v-model="localDialog" max-width="1200px">
    <v-card>
      <v-card-title>
        <v-row>
          <v-col cols="9">
            <v-icon>mdi-transit-connection-variant</v-icon>
            {{ agent.label }}
          </v-col>
          <v-col cols="3" class="text-right checkbox-right">
            <v-checkbox :label="enabledLabel()" hide-details density="compact" color="green" v-model="agent.enabled"
              v-if="agent.data.has_toggle" @update:modelValue="save(false)"></v-checkbox>
          </v-col>
        </v-row>

      </v-card-title>


      <v-card-text>

        <v-row>
          <v-col cols="4">
            <v-tabs v-model="tab" color="primary" direction="vertical">
              <v-tab
                v-for="item in tabs"
                :key="item.name"
                v-model="tab"
                :value="item.name"
                :prepend-icon="item.parentKey ? 'mdi-subdirectory-arrow-right' : item.icon"
                :class="item.parentKey ? 'pl-6 text-body-2' : ''"
              >
                <span>{{ item.label }}</span>
                <v-chip class="ml-2" size="x-small" variant="tonal" label color="secondary" v-if="item.action.subtitle">{{ item.action.subtitle }}</v-chip>
              </v-tab>
            </v-tabs>
          </v-col>
          <v-col cols="8" class="scrollable-content">
            <v-window v-model="tab">
              <v-window-item :value="item.name" v-for="item in tabs" :key="item.name">
                <v-select v-if="agent.data.requires_llm_client && tab === '_config'" v-model="selectedClient" :items="agent.data.client" label="Client"  @update:modelValue="save(false)"></v-select>

                <!-- Registry tab — render the agent-supplied custom
                     component (if any), otherwise the generic registry. The
                     component name is declared on the action via
                     ``dynamic_registry_component`` (Python). -->
                <component
                  v-if="tab === item.name && isRegistryTab && agent.data.actions[item.name]"
                  :is="resolveRegistryComponent(item.name)"
                  :children="dynamicChildrenForCurrentTab"
                  :description="agent.data.actions[item.name].description"
                  @add="onAddDynamicChild(item.name, $event)"
                  @remove="onRemoveDynamicChild(item.name, $event)"
                  @rename="onRenameDynamicChild(item.name, $event[0], $event[1])"
                  @refresh-voices="onRefreshBackendVoices"
                />

                <v-sheet v-for="(action, key) in actionsForTab" :key="key" density="compact"
                         v-show="!(isRegistryTab && tab === item.name && key === item.name)">
                  <div v-if="testActionConditional(action)">
                    <div>
                      <v-checkbox v-if="!actionAlwaysVisible(key, action) && !action.container" :label="agent.data.actions[key].label" :messages="agent.data.actions[key].description" density="compact" color="primary" v-model="action.enabled" @update:modelValue="save(false)">
                        <!-- template details slot -->
                        <template v-slot:message="{ message }">
                          <div class="text-caption text-grey mb-8">{{ message }}</div>
                        </template>

                      </v-checkbox>
                      <div v-else-if="action.container" class="text-muted mt-2">
                        {{ agent.data.actions[key].description }}

                        <p v-if="agent.data.actions[key].warning" class="text-warning mt-2 text-caption">
                          <v-icon size="x-small">mdi-alert-circle-outline</v-icon>
                          {{ agent.data.actions[key].warning }}
                        </p>

                        <div v-if="agent.data.actions[key].tools && agent.data.actions[key].tools.length" class="mt-3 mb-1">
                          <v-btn
                            v-for="tool in agent.data.actions[key].tools"
                            :key="tool.action_name"
                            :prepend-icon="tool.icon"
                            variant="tonal"
                            size="small"
                            color="primary"
                            class="mr-2"
                            @click="callAgentTool(tool.action_name, tool.arguments || [])"
                          >{{ tool.label }}</v-btn>
                        </div>
                      </div>
                    </div>
                    <div class="mt-2">

                      <div v-if="action.container && action.can_be_disabled">
                        <v-checkbox :label="'Enable '+action.label" color="primary" v-model="action.enabled" @update:modelValue="save(false)" density="compact">
                          <!-- template details slot -->
                        </v-checkbox>
                      </div>

                      <div v-for="(action_config, config_key) in agent.data.actions[key].config" :key="config_key">
                        <div v-if="config_key !== 'dynamic_children' && (action.enabled || actionAlwaysVisible(key, action)) && testConfigConditional(action_config)">
                          <!-- render config widgets based on action_config.type (int, str, bool, float) -->

                          <div v-if="action_config.title">
                            <div class="text-caption text-muted text-uppercase">{{ action_config.title }}</div>
                            <v-divider class="mb-2"></v-divider>
                          </div>

                          <!-- text -->
                          <v-text-field
                            v-if="action_config.type === 'text' && action_config.choices === null" 
                            v-model="action.config[config_key].value" 
                            :label="action_config.label" 
                            :hint="action_config.description" 
                            density="compact" 
                            @keyup="save(false)"
                            @blur="save(action_config.save_on_change ? true : false)"
                            class="mt-3"
                            ></v-text-field>

                          <!-- password -->
                          <v-text-field
                            v-else-if="action_config.type === 'password'" 
                            v-model="action.config[config_key].value" 
                            :label="action_config.label" 
                            :hint="action_config.description" 
                            density="compact" 
                            type="password"
                            @keyup="save(false)"
                            @blur="save(action_config.save_on_change ? true : false)"
                            class="mt-3"
                            ></v-text-field>

                          <!-- blob -->
                          <v-textarea 
                            v-else-if="action_config.type === 'blob'" 
                            v-model="action.config[config_key].value" 
                            :label="action_config.label" 
                            :hint="action_config.description" 
                            density="compact" 
                            @keyup="save(false)"
                            rows="5"
                            class="mt-3"
                            ></v-textarea>


                          <!-- autocomplete -->
                          <v-autocomplete 
                          v-else-if="action_config.type === 'autocomplete' && action_config.choices !== null" 
                            v-model="action.config[config_key].value" 
                            :items="action_config.choices" 
                            :label="action_config.label" 
                            :hint="action_config.description" 
                            item-title="label" 
                            item-value="value" 
                            @update:modelValue="save(false)" 
                            class="mt-3"
                          ></v-autocomplete>

                          <!-- world state template selector -->
                          <v-autocomplete 
                            v-else-if="action_config.type === 'wstemplate'" 
                            v-model="action.config[config_key].value" 
                            :items="wstemplateChoices(action_config)" 
                            :label="action_config.label" 
                            :hint="action_config.description" 
                            item-title="label" 
                            item-value="value" 
                            @update:modelValue="save(action_config.save_on_change ? true : false)" 
                            class="mt-3"
                          >
                            <template v-slot:item="{ props, item }">
                              <v-list-item v-bind="props" :title="item.raw.label" :subtitle="item.raw.subtitle"></v-list-item>
                            </template>
                          </v-autocomplete>

                          <!-- select -->
                          <v-select 
                            v-else-if="action_config.type === 'text' && action_config.choices !== null" 
                            v-model="action.config[config_key].value" 
                            :items="action_config.choices" 
                            :label="action_config.label" 
                            :hint="action_config.description" 
                            item-title="label" 
                            item-value="value" 
                            :menu-props="{ maxHeight: 480 }"
                            @update:modelValue="save(action_config.save_on_change ? true : false)" 
                            class="mt-3"
                          ></v-select>

                          <!-- flags -->
                          <v-select 
                            v-else-if="action_config.type === 'flags'"
                            v-model="action.config[config_key].value" 
                            :items="action_config.choices" 
                            :label="action_config.label" 
                            :hint="action_config.description" 
                            item-title="label" 
                            item-subtitle="help"
                            multiple
                            chips
                            item-value="value" 
                            @update:modelValue="save(false)" 
                            class="mt-3"
                          >
                          </v-select>

                          <!-- number (graduated) -->
                          <GraduatedSlider
                            v-if="action_config.type === 'number' && action_config.graduations"
                            v-model="action.config[config_key].value"
                            :label="action_config.label"
                            :hint="action_config.description"
                            :min="action_config.min"
                            :max="action_config.max"
                            :graduations="action_config.graduations"
                            density="compact"
                            @update:modelValue="save(false)"
                            color="primary"
                            thumb-label="always"
                            class="mt-3"
                          />
                          <!-- number -->
                          <v-slider
                            v-else-if="action_config.type === 'number'"
                            v-model="action.config[config_key].value"
                            :label="action_config.label"
                            :hint="action_config.description"
                            :min="action_config.min"
                            :max="action_config.max"
                            :step="action_config.step || 1"
                            density="compact"
                            @update:modelValue="save(false)"
                            color="primary"
                            thumb-label="always"
                            class="mt-3"
                          ></v-slider>

                          <!-- boolean -->
                          <v-checkbox 
                            v-if="action_config.type === 'bool'" 
                            v-model="action.config[config_key].value" 
                            :label="action_config.label" 
                            :messages="action_config.description" 
                            density="compact" @update:modelValue="save(false)" color="primary">
                            <!-- template details slot -->
                            <template v-slot:message="{ message }">
                              <span class="text-caption text-grey">{{ message }}</span>
                              <span v-if="action_config.expensive" class="text-warning mt-2 text-caption">
                                <v-icon size="x-small">mdi-alert-circle-outline</v-icon>
                                Potential for many additional prompts.
                              </span>
                            </template>


                          </v-checkbox>

                          <!-- vector2 -->
                          <v-row v-if="action_config.type === 'vector2'" class="mt-3">
                            <v-col cols="12">
                              <div class="text-caption text-muted text-uppercase">{{ action_config.label }}</div>
                            </v-col>
                            <v-col :cols="action_config.choices ? 5 : 6">
                              <v-number-input 
                                v-model="action.config[config_key].value[0]" 
                                hide-details
                                type="number"
                                density="compact"
                                @update:modelValue="save(false)" 
                              ></v-number-input>
                            </v-col>
                            <v-col :cols="action_config.choices ? 5 : 6">
                              <v-number-input 
                                v-model="action.config[config_key].value[1]" 
                                hide-details
                                type="number"
                                density="compact"
                                @update:modelValue="save(false)" 
                              ></v-number-input>
                            </v-col>
                            <v-col cols="2" v-if="action_config.choices" class="d-flex align-center justify-center">
                              <v-menu location="bottom end">
                                <template v-slot:activator="{ props }">
                                  <v-chip
                                    v-bind="props"
                                    size="small"
                                    variant="tonal"
                                    color="primary"
                                    class="px-2"
                                  >
                                    <v-icon icon="mdi-menu-down"></v-icon>
                                  </v-chip>
                                </template>
                                <v-list density="compact">
                                  <v-list-item
                                    v-for="(choice, i) in action_config.choices"
                                    :key="i"
                                    :value="i"
                                    @click="action.config[config_key].value = [...choice.value]; save(false)"
                                  >
                                    <v-list-item-title>{{ choice.label }}</v-list-item-title>
                                  </v-list-item>
                                </v-list>
                              </v-menu>
                            </v-col>
                          </v-row>

                          <!-- table -->
                          <ConfigWidgetTable v-else-if="action_config.type === 'table'" :columns="action_config.columns" :default_values="action.config[config_key].value" :label="action_config.label" :description="action_config.description" @save="(values) => { action.config[config_key].value = values; save(false); }" />

                          <!-- unified_api_key -->
                          <ConfigWidgetUnifiedApiKey
                            v-else-if="action_config.type === 'unified_api_key'"
                            :config-path="action_config.value"
                            :title="action_config.label"
                            :app-config="appConfigValue"
                            class="mt-3"
                          />

                          <v-alert v-if="action_config.note != null" variant="outlined" density="compact" :color="action_config.note.color || 'muted'" :icon="action_config.note.icon">
                            <div class="text-caption text-mutedheader">{{ action_config.note.title || action_config.label }}</div>
                            <span class="text-muted text-caption">{{ action_config.note.text }}</span>
                          </v-alert>
                          <div v-else-if="action_config.note_on_value != null">
                            <div v-for="(note, key) in action_config.note_on_value" :key="key">
                              <v-alert v-if="testNoteConditional(action_config, action.config[config_key], key, note)" variant="outlined" density="compact" :color="note.color || 'muted'" class="my-2" :icon="note.icon">
                                <span :class="['text-caption text-uppercase mr-2']">
                                  {{ key.toLowerCase() === 'true' ? 'ENABLED' : key.replace(/_/g, ' ') }}
                                </span>
                                <span class="text-muted text-caption">
                                  {{ note.text }}
                                </span>
                              </v-alert>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </v-sheet>
              </v-window-item>
            </v-window>
          </v-col>
        </v-row>

        <v-row>
          <v-col cols="12">
            <v-alert type="warning" variant="outlined" density="compact" v-if="agent.data.experimental">
              <!-- small icon -->
              <span class="text-caption">
                This agent is currently experimental and may significantly decrease performance and / or require
                strong LLMs to function properly.
              </span>
            </v-alert>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
  
<script>
import {getProperty} from 'dot-prop';
import ConfigWidgetTable from './ConfigWidgetTable.vue';
import ConfigWidgetUnifiedApiKey from './ConfigWidgetUnifiedApiKey.vue';
import GraduatedSlider from './GraduatedSlider.vue';
import DynamicAgentRegistry from './DynamicAgentRegistry.vue';
import TTSOpenAICompatibleBackends from './TTSOpenAICompatibleBackends.vue';
import { registerRuntimeCompiler } from 'vue';

// Mapping from `AgentAction.dynamic_registry_component` (declared by the
// Python agent action) to the Vue component that should render that
// registry's management tab. Anything not in this map (or unset on the
// action) falls back to the generic DynamicAgentRegistry.
const REGISTRY_COMPONENTS = {
  TTSOpenAICompatibleBackends,
};

export default {
  props: {
    dialog: Boolean,
    formTitle: String,
    templates: Object,
    appConfig: Object,
  },
  components: {
    ConfigWidgetTable,
    ConfigWidgetUnifiedApiKey,
    GraduatedSlider,
    DynamicAgentRegistry,
    TTSOpenAICompatibleBackends,
  },
  inject: ['state', 'getWebsocket', 'callAgentTool'],
  data() {
    return {
      localDialog: this.state.dialog,
      selectedClient: null,
      tab: "_config",
      // deep clone to avoid mutations being immediately reflected in the source object
      agent: JSON.parse(JSON.stringify(this.state.currentAgent))
    };
  },
  computed: {
    tabs() {
      // will cycle through all actions, and each each action that has `container` = True, will be added to the tabs
      // will always add a general tab for the general agent settings.
      // Tabs whose action has a `parent_key` are grouped (rendered indented)
      // immediately after their parent tab so dynamic-registry children
      // appear visually attached to their registry tab.

      const tabs = [{ name: "_config", label: "General", icon: "mdi-cog", action: {}, parentKey: null }];
      const childrenByParent = {};

      for (const key in this.agent.actions) {
        const action = this.agent.actions[key];
        if (!action.container) continue;
        if (this.testActionConditional(action) === false) continue;

        if (action.parent_key) {
          if (!childrenByParent[action.parent_key]) childrenByParent[action.parent_key] = [];
          childrenByParent[action.parent_key].push({
            name: key,
            label: action.label,
            icon: action.icon,
            action,
            parentKey: action.parent_key,
          });
          continue;
        }
        tabs.push({ name: key, label: action.label, icon: action.icon, action, parentKey: null });
      }

      // splice children in directly after their parent.
      // Children keep their *insertion order* (the order they were added in
      // the dynamic_children blob) — sorting by label would reorder tabs
      // when a user renames a backend, which is jarring.
      const result = [];
      for (const tab of tabs) {
        result.push(tab);
        const children = childrenByParent[tab.name];
        if (children && children.length) {
          for (const child of children) result.push(child);
        }
      }
      return result;
    },

    dynamicChildrenForCurrentTab() {
      // For a registry tab, return the [{slug, label}] list of its children.
      const action = this.agent.actions[this.tab];
      if (!action || !action.config || !action.config.dynamic_children) return null;
      try {
        const parsed = JSON.parse(action.config.dynamic_children.value || '[]');
        return Array.isArray(parsed) ? parsed : [];
      } catch (_e) {
        return [];
      }
    },

    isRegistryTab() {
      return this.dynamicChildrenForCurrentTab !== null;
    },

    actionsForTab() {
      // if tab is _config, return _config and all actions that don't set container = True
      // otherwise, return the action that matches the tab name
      let actions = {}
      if (this.tab === "_config") {
        
        if(this.agent.actions["_config"]){
          actions["_config"] = this.agent.actions["_config"];

        }
        for (let key in this.agent.actions) {
          let action = this.agent.actions[key];
          if (!action.container) {
            actions[key] = action;
          }
        }
      } else {
        actions[this.tab] = this.agent.actions[this.tab];
      }

      return actions;
    },
    appConfigValue() {
      return this.appConfig || null;
    }
  },
  watch: {
    'state.dialog': {
      immediate: true,
      handler(newVal) {
        this.localDialog = newVal;
        if(newVal) {
          this.selectedClient = typeof(this.agent.client) === 'object' && this.agent.client.client ? this.agent.client.client.value : this.agent.client;
        }
      }
    },
    'state.currentAgent': {
      immediate: true,
      handler(newVal) {
        // deep clone whenever a new agent is provided (e.g. opening a different agent)
        this.agent = JSON.parse(JSON.stringify(newVal));
      }
    },
    localDialog(newVal) {
      // whenever the dialog closes, persist changes
      if (!newVal) {
        this.finalizeSave();
      }
      this.$emit('update:dialog', newVal);
    }
  },
  methods: {
    enabledLabel() {
      if (this.agent.enabled) {
        return 'Enabled';
      } else {
        return 'Enable';
      }
    },
    actionAlwaysVisible(actionName, action) {
      if (actionName.charAt(0) === '_' || action.container || !action.can_be_disabled) {
        return true;
      } else {
        return false;
      }
    },

    testActionConditional(action) {
      if(action.condition == null)
        return true;

      if(typeof(this.agent.client) !== 'object')
        return true;

      let value = getProperty(this.agent.actions, action.condition.attribute+".value");

      if(Array.isArray(action.condition.value)) {
        return action.condition.value.some(v => v == value);
      } else {
        return value == action.condition.value;
      }
    },

    testConfigConditional(config) {
      if(config.condition == null)
        return true;

      let value = getProperty(this.agent.actions, config.condition.attribute+".value");

      if(Array.isArray(config.condition.value)) {
        return config.condition.value.some(v => v == value);
      } else {
        return value == config.condition.value;
      }
    },

    testNoteConditional(action_config, current_config, key, note) {
      // Handle boolean values which have string keys in the JSON object
      let test = current_config.value == key || String(current_config.value) == key;
      // console.log("testNoteConditional: ", test, action_config, current_config, key, note);
      return test;
    },

    close() {
      // explicitly close via code (e.g. OK button). Persist changes first.
      this.finalizeSave();
      this.$emit('update:dialog', false);
    },

    // called by input widgets to update in-memory copy. No persistence happens here.
    save(finalize = false) {
      if (finalize) {
        this.finalizeSave();
        return;
      }

      if (this.selectedClient != null) {
        if (typeof this.agent.client === 'object') {
          if (this.agent.client.client != null) {
            this.agent.client.client.value = this.selectedClient;
          }
        } else {
          this.agent.client = this.selectedClient;
        }
      }
      // No emit - persistence postponed until dialog closes
    },

    // persist edited agent back to parent component
    finalizeSave() {
      // propagate selected client before emit just in case save() was never triggered
      this.save();
      this.$emit('save', this.agent);
    },

    // ----------------------------------------------------------------
    // Dynamic-action registry (e.g., TTS OpenAI-compatible backends)
    // ----------------------------------------------------------------

    onAddDynamicChild(actionKey, label) {
      this.getWebsocket().send(JSON.stringify({
        type: 'agent_config',
        action: 'register_child',
        agent_type: this.agent.name,
        action_key: actionKey,
        label,
      }));
    },

    onRemoveDynamicChild(actionKey, slug) {
      this.getWebsocket().send(JSON.stringify({
        type: 'agent_config',
        action: 'unregister_child',
        agent_type: this.agent.name,
        action_key: actionKey,
        slug,
      }));
    },

    onRenameDynamicChild(actionKey, slug, label) {
      this.getWebsocket().send(JSON.stringify({
        type: 'agent_config',
        action: 'rename_child',
        agent_type: this.agent.name,
        action_key: actionKey,
        slug,
        label,
      }));
    },

    onRefreshBackendVoices(slug) {
      this.getWebsocket().send(JSON.stringify({
        type: 'tts',
        action: 'refresh_backend_voices',
        slug,
      }));
    },

    /**
     * Resolve the Vue component used to render a registry tab. Looks at
     * ``dynamic_registry_component`` declared by the agent action; falls back
     * to the generic ``DynamicAgentRegistry`` when unset or unknown.
     */
    resolveRegistryComponent(actionKey) {
      const declared = this.agent?.data?.actions?.[actionKey]?.dynamic_registry_component;
      if (declared && REGISTRY_COMPONENTS[declared]) {
        return REGISTRY_COMPONENTS[declared];
      }
      return DynamicAgentRegistry;
    },

    /**
     * Updates only the choices arrays in the local agent object from updated agent data,
     * preserving all user-entered values to avoid overwriting unsaved changes.
     *
     * Also reconciles dynamic-action-registry membership: new children that
     * appeared server-side (via Add Backend) get cloned into the local agent;
     * children removed server-side get dropped; the registry's dynamic_children
     * blob value is synced so a subsequent close-and-save round-trip preserves
     * the new state.
     */
    updateChoicesOnly(updatedAgent) {
      if (!updatedAgent?.data?.actions || !this.agent?.data?.actions) {
        return;
      }

      // Update choices in data.actions (the schema/definition)
      for (const actionKey in updatedAgent.data.actions) {
        const updatedAction = updatedAgent.data.actions[actionKey];
        if (!updatedAction?.config) continue;

        // Ensure local agent has the action structure
        if (!this.agent.data.actions[actionKey]) {
          this.agent.data.actions[actionKey] = {};
        }
        if (!this.agent.data.actions[actionKey].config) {
          this.agent.data.actions[actionKey].config = {};
        }

        // Update choices for each config item
        for (const configKey in updatedAction.config) {
          const updatedConfig = updatedAction.config[configKey];
          // Only update if choices exist and are not null/undefined
          if (updatedConfig?.choices != null) {
            // Ensure the config object exists
            if (!this.agent.data.actions[actionKey].config[configKey]) {
              this.agent.data.actions[actionKey].config[configKey] = {};
            }
            // Update only the choices property, preserving all other config properties
            // Create new array reference to ensure Vue reactivity
            this.agent.data.actions[actionKey].config[configKey].choices = Array.isArray(updatedConfig.choices)
              ? [...updatedConfig.choices]
              : updatedConfig.choices;
          }
        }
      }

      this.syncDynamicChildren(updatedAgent);
    },

    /**
     * Reconcile dynamic-registry children between server state and the modal's
     * editable copy. Touches only entries that have ``parent_key`` set
     * (synthesized children) plus the ``dynamic_children`` blob value on the
     * registry action — user edits to ordinary action config values are
     * preserved.
     */
    syncDynamicChildren(updatedAgent) {
      if (!updatedAgent?.actions || !this.agent?.actions) return;
      const serverActions = updatedAgent.actions;
      const serverDataActions = updatedAgent.data?.actions || {};

      // 1. Sync dynamic_children blob values on every registry action
      for (const actionKey in serverActions) {
        const serverAction = serverActions[actionKey];
        const serverBlobField = serverAction?.config?.dynamic_children;
        if (!serverBlobField) continue;
        if (!this.agent.actions[actionKey]?.config?.dynamic_children) continue;
        this.agent.actions[actionKey].config.dynamic_children.value =
          serverBlobField.value;
        if (this.agent.data?.actions?.[actionKey]?.config?.dynamic_children) {
          this.agent.data.actions[actionKey].config.dynamic_children.value =
            serverBlobField.value;
        }
      }

      // 2. Add any synthesized children that aren't in the local copy yet
      for (const actionKey in serverActions) {
        const serverAction = serverActions[actionKey];
        if (!serverAction || !serverAction.parent_key) continue;
        if (this.agent.actions[actionKey]) continue;
        // Deep-clone so server-side mutations don't leak into the local copy
        this.agent.actions[actionKey] = JSON.parse(JSON.stringify(serverAction));
        if (serverDataActions[actionKey]) {
          this.agent.data.actions[actionKey] = JSON.parse(
            JSON.stringify(serverDataActions[actionKey])
          );
        }
      }

      // 3. Drop synthesized children that the server has removed
      for (const actionKey of Object.keys(this.agent.actions)) {
        const localAction = this.agent.actions[actionKey];
        if (!localAction || !localAction.parent_key) continue;
        if (!serverActions[actionKey]) {
          delete this.agent.actions[actionKey];
          if (this.agent.data?.actions?.[actionKey]) {
            delete this.agent.data.actions[actionKey];
          }
          // If the deleted backend's tab was active, fall back to General
          if (this.tab === actionKey) this.tab = '_config';
        }
      }
    },

    wstemplateChoices(action_config) {
      // Resolve template bucket by desired template type; bail early if not available
      const bucket = this.templates?.by_type?.[action_config?.wstemplate_type];
      if (!bucket) return [];

      // Build group uid -> display name map for subtitles
      const groupNameByUid = Object.fromEntries(
        (this.templates?.managed?.groups ?? [])
          .filter(Boolean)
          .map(g => [g.uid, g.name || g.uid])
      );

      // Optional filter: object of { dot.path: value | [values...] }
      const filter = action_config?.wstemplate_filter;
      const hasFilter = filter && typeof filter === 'object' && Object.keys(filter).length > 0;

      const items = [];
      for (const [uid, template] of Object.entries(bucket)) {
        // Apply filter if present
        if (hasFilter) {
          let match = true;
          for (const key in filter) {
            const expected = filter[key];
            const actual = getProperty(template, key);
            if (Array.isArray(expected)) {
              if (!expected.some(v => v == actual)) { match = false; break; }
            } else if (expected != actual) {
              match = false; break;
            }
          }
          if (!match) continue;
        }

        // Item for v-autocomplete with group name as subtitle
        const subtitle = template?.group ? (groupNameByUid[template.group] || template.group) : undefined;
        items.push({ label: template?.name || uid, value: uid, subtitle });
      }
      return items;
    }
  }
}
</script>

<style>
.scrollable-content {
  overflow-y: auto;
  max-height: 80vh;
  padding-right: 16px;
}

.checkbox-right {
  display: flex;
  justify-content: flex-end;
}
</style>