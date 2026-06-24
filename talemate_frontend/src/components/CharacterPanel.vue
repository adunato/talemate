<template>
    <v-list density="compact" slim>
        <v-list-subheader color="grey">
            <v-icon color="primary" class="mr-1">mdi-account-group</v-icon>
            Characters
        </v-list-subheader>

        <v-progress-linear v-if="loading" indeterminate color="primary" />

        <v-list-item
            v-for="character in characters"
            :key="character.name"
            :disabled="pendingCharacter === character.name"
        >
            <template v-slot:prepend>
                <v-icon>mdi-account</v-icon>
            </template>

            <v-list-item-title>{{ character.name }}</v-list-item-title>
            <v-list-item-subtitle>
                <v-chip
                    label
                    size="x-small"
                    :color="character.is_player ? 'info' : 'warning'"
                    variant="tonal"
                >
                    {{ character.is_player ? 'Player' : 'AI' }}
                </v-chip>
                <v-chip
                    v-if="character.active"
                    label
                    size="x-small"
                    color="success"
                    variant="tonal"
                    class="ml-1"
                >
                    Active
                </v-chip>
            </v-list-item-subtitle>

            <template v-slot:append>
                <v-btn
                    v-if="character.active"
                    size="small"
                    color="secondary"
                    variant="tonal"
                    :loading="pendingCharacter === character.name"
                    @click.stop="setCharacterActive(character, false)"
                >
                    Deactivate
                </v-btn>
                <v-btn
                    v-else
                    size="small"
                    color="primary"
                    variant="tonal"
                    :loading="pendingCharacter === character.name"
                    @click.stop="setCharacterActive(character, true)"
                >
                    Activate
                </v-btn>
            </template>
        </v-list-item>

        <v-list-item v-if="!loading && characters.length === 0">
            <v-list-item-title class="text-muted">No characters in this scene.</v-list-item-title>
        </v-list-item>
    </v-list>
</template>

<script>
export default {
    name: 'CharacterPanel',
    inject: [
        'getWebsocket',
        'registerMessageHandler',
        'unregisterMessageHandler',
    ],
    props: {
        open: {
            type: Boolean,
            default: false,
        },
    },
    data() {
        return {
            characterList: {
                characters: {},
            },
            loading: false,
            pendingCharacter: null,
        };
    },
    computed: {
        characters() {
            return Object.values(this.characterList?.characters || {}).sort((a, b) => {
                if (a.is_player !== b.is_player) {
                    return a.is_player ? -1 : 1;
                }
                return a.name.localeCompare(b.name);
            });
        },
    },
    watch: {
        open(isOpen) {
            if (isOpen) {
                this.requestCharacterList();
            }
        },
    },
    methods: {
        requestCharacterList() {
            this.loading = true;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: 'get_character_list',
            }));
        },
        setCharacterActive(character, active) {
            this.pendingCharacter = character.name;
            this.getWebsocket().send(JSON.stringify({
                type: 'world_state_manager',
                action: active ? 'activate_character' : 'deactivate_character',
                name: character.name,
            }));
        },
        handleMessage(message) {
            if (message.type !== 'world_state_manager') {
                return;
            }

            if (message.action === 'character_list') {
                this.characterList = message.data;
                this.loading = false;
                this.pendingCharacter = null;
            } else if (
                message.action === 'character_activated'
                || message.action === 'character_deactivated'
                || message.action === 'character_deleted'
                || message.action === 'character_renamed'
            ) {
                this.requestCharacterList();
            }
        },
    },
    created() {
        this.registerMessageHandler(this.handleMessage);
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
    },
};
</script>
