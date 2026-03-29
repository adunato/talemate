<template>
    <v-menu location="top">
        <template v-slot:activator="{ props }">
            <v-btn class="hotkey mx-1" v-bind="props" :disabled="disabled" color="primary" icon variant="text">
                <v-icon>mdi-bullhorn</v-icon>
            </v-btn>
        </template>
        <v-list>
            <v-list-subheader>Director Actions</v-list-subheader>
            <!-- Generate dynamic choices  -->
            <v-list-item
                density="compact"
                @click="actionRequestDynamicChoices"
                prepend-icon="mdi-tournament"
            >
                <v-list-item-title>Generate dynamic actions<v-chip variant="text" color="highlight5" class="ml-1" size="x-small">Ctrl: Provide direction</v-chip></v-list-item-title>
                <v-list-item-subtitle>{{ getActAsCharacterName() }}</v-list-item-subtitle>
            </v-list-item>
            <!-- Trigger scene direction turn -->
            <v-list-item
                density="compact"
                @click="actionSceneDirectionTurn"
                prepend-icon="mdi-movie-play"
                :disabled="!sceneDirectionEnabled"
            >
                <v-list-item-title>Scene direction turn<v-chip variant="text" color="highlight5" class="ml-1" size="x-small">Ctrl: Provide direction</v-chip></v-list-item-title>
                <v-list-item-subtitle>Manually trigger a scene direction turn</v-list-item-subtitle>
            </v-list-item>
            <!-- Generate long progress -->
            <v-list-item
                density="compact"
                @click="openScenePlanDialog"
                prepend-icon="mdi-movie-open"
            >
                <v-list-item-title>Generate long progress</v-list-item-title>
                <v-list-item-subtitle>Plan and generate a whole progress arc</v-list-item-subtitle>
            </v-list-item>
        </v-list>
    </v-menu>

    <!-- narrative direction input -->
    <RequestInput ref="instructionsInput" title="Director Prompt"
        :instructions="'Instructions - Provide an instruction for the director to follow for this action'"
        input-type="multiline" icon="mdi-dice-multiple" :size="750" @continue="applyDirection" />

    <!-- Scene plan dialog -->
    <v-dialog v-model="scenePlanDialog" max-width="800">
        <v-card>
            <v-card-title><v-icon class="mr-2">mdi-movie-open</v-icon>Generate Long Progress</v-card-title>
            <v-card-text>
                <div class="text-muted mb-4">Automatically plans and generates multiple turns of narrative progress based on your instructions. The director will create an outline and then execute each turn sequentially.</div>
                <v-textarea
                    v-model="scenePlanInstructions"
                    label="Scene instructions"
                    hint="Describe what should happen in the scene"
                    rows="8"
                    auto-grow
                ></v-textarea>
                <v-slider
                    v-model="scenePlanBeats"
                    label="Number of turns"
                    :min="3"
                    :max="24"
                    :step="1"
                    thumb-label="always"
                    color="primary"
                    class="mt-4"
                ></v-slider>
                <v-slider
                    v-model="scenePlanDialogueRatio"
                    label="Dialogue ratio"
                    :min="0"
                    :max="100"
                    :step="10"
                    thumb-label="always"
                    color="primary"
                    class="mt-4"
                >
                    <template v-slot:thumb-label="{ modelValue }">{{ modelValue }}%</template>
                </v-slider>

                <!-- Warning: narrator progress_story generation length too low -->
                <v-alert
                    v-if="narratorProgressStoryLength < minRecommendedNarratorLength"
                    variant="outlined"
                    density="compact"
                    color="warning"
                    class="mb-3"
                >
                    <div class="text-muted">
                        The narrator's <strong>Progress Story</strong> generation length is set to <strong>{{ narratorProgressStoryLength }}</strong> tokens. For best results with long progress generation, consider increasing it to at least <strong>{{ minRecommendedNarratorLength }}</strong>.
                        <v-btn size="x-small" variant="text" color="warning" @click="openAgentSettings('narrator', 'generation_override')">Open narrator settings</v-btn>
                    </div>
                </v-alert>

                <!-- Warning: characters missing acting instructions -->
                <v-alert
                    v-if="charactersMissingActingInstructions.length > 0"
                    variant="outlined"
                    density="compact"
                    color="warning"
                    class="mb-3"
                >
                    <div class="text-muted">
                        The following characters are missing <strong>Acting instructions</strong>:
                        <strong>{{ charactersMissingActingInstructions.join(', ') }}</strong>.
                        If the director calls on them to act, the quality of their dialogue and actions may be diminished.
                    </div>
                </v-alert>

                <v-btn-toggle
                    v-model="scenePlanMode"
                    mandatory
                    density="compact"
                    color="primary"
                    class="mb-4"
                >
                    <v-btn value="generate_arc" size="small">
                        <v-icon start>mdi-directions-fork</v-icon>
                        Turn by turn
                    </v-btn>
                    <v-btn value="generate_arc_expand" size="small">
                        <v-icon start>mdi-lightning-bolt</v-icon>
                        Expand (fast)
                    </v-btn>
                </v-btn-toggle>

                <v-alert variant="outlined" density="compact" color="primary" class="mb-3">
                    <div class="text-muted" v-if="scenePlanMode === 'generate_arc'">Turn by turn mode: the director executes each beat individually through the narrator and conversation agents. Slower but uses full per-turn context.</div>
                    <div class="text-muted" v-else>Expand mode: beats are expanded into prose in chunks. Much faster but with less per-turn context injection.</div>
                </v-alert>

                <v-alert variant="outlined" density="compact" color="primary" class="mb-3">
                    <div class="text-muted">This may generate actions and dialogue for player controlled characters as well.</div>
                </v-alert>
            </v-card-text>
            <v-card-actions>
                <v-spacer></v-spacer>
                <v-btn color="cancel" prepend-icon="mdi-close" @click="scenePlanDialog = false">Cancel</v-btn>
                <v-btn color="primary" prepend-icon="mdi-play" @click="actionCreateScenePlan" :disabled="!scenePlanInstructions.trim()">Plan &amp; Generate</v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>

</template>

<script>
import RequestInput from './RequestInput.vue';

export default {
    name: "SceneToolsDirector",
    components: {
        RequestInput,
    },
    props: {
        npcCharacters: Array,
        sceneCharacters: Array,
        disabled: Boolean,
        agentStatus: Object,
    },
    inject: ['getWebsocket', 'getActAsCharacterName', 'openDirectorConsole', 'openAgentSettings'],
    data() {
        return {
            scenePlanDialog: false,
            scenePlanInstructions: '',
            scenePlanBeats: 8,
            scenePlanDialogueRatio: 40,
            scenePlanMode: 'generate_arc',
            minRecommendedNarratorLength: 1024,
        }
    },
    computed: {
        sceneDirectionEnabled() {
            return this.agentStatus?.director?.actions?.scene_direction?.enabled || false;
        },
        charactersMissingActingInstructions() {
            if (!this.sceneCharacters) return [];
            return this.sceneCharacters
                .filter(c => !c.dialogue_instructions)
                .map(c => c.name);
        },
        narratorProgressStoryLength() {
            // fallback 512 matches backend default in NarratorAgent.init_actions()
            return this.agentStatus?.narrator?.actions?.generation_override?.config?.length_progress_story?.value ?? 512;
        },
    },
    methods: {

        requestDirection(params) {
            this.$nextTick(() => {
                this.$refs.instructionsInput.openDialog(params);
            });
        },

        applyDirection(input, params){
            let callback = this[`action${params.action}`];
            if(callback){
                callback({}, input, params);
            }
        },

        // Director actions

        /**
         * Progress the story
         * @method actionRequestDynamicChoices
         * @param {string} instructions - The direction to progress the story in
         */

        actionRequestDynamicChoices(ev, instructions="") {

            if (ev.ctrlKey) {
                this.requestDirection({action: 'RequestDynamicChoices'});
                return;
            }

            this.getWebsocket().send(JSON.stringify(
                {
                    type: 'director',
                    action: 'request_dynamic_choices',
                    instructions: instructions || "",
                    character: this.getActAsCharacterName(),
                }
            ));
        },

        /**
         * Manually trigger a scene direction turn
         * @method actionSceneDirectionTurn
         * @param {string} instructions - One-off instructions for this turn
         */

        actionSceneDirectionTurn(ev, instructions="") {

            if (ev.ctrlKey) {
                this.requestDirection({action: 'SceneDirectionTurn'});
                return;
            }

            this.getWebsocket().send(JSON.stringify(
                {
                    type: 'director',
                    action: 'scene_direction_turn',
                    instructions: instructions || "",
                }
            ));
        },

        openScenePlanDialog() {
            this.scenePlanInstructions = '';
            this.scenePlanBeats = 8;
            this.scenePlanMode = 'generate_arc';
            this.scenePlanDialogueRatio = Math.round((this.agentStatus?.director?.actions?.chat?.config?.generate_arc_dialogue_ratio?.value ?? 0.4) * 100);
            this.scenePlanDialog = true;
        },

        actionCreateScenePlan() {
            this.scenePlanDialog = false;
            this.getWebsocket().send(JSON.stringify({
                type: 'director',
                action: 'chat_create_generate_arc',
                instructions: this.scenePlanInstructions,
                beat_count: this.scenePlanBeats,
                dialogue_ratio: this.scenePlanDialogueRatio / 100,
                mode: this.scenePlanMode,
            }));
            this.openDirectorConsole();
        },
    }
}

</script>
