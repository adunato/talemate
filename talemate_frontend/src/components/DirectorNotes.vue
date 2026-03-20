<template>
    <div class="mt-4">
        <div class="text-caption text-muted mb-2">
            Temporary instructions the director follows. Each note expires after a set number of scene turns (narrator or character messages).
        </div>

            <!-- existing notes table -->
            <v-table v-if="notes.length > 0" density="compact" class="mb-3">
                <thead>
                    <tr>
                        <th>Instruction</th>
                        <th style="width: 140px;">Remaining</th>
                        <th style="width: 48px;"></th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="note in notes" :key="note.id">
                        <td>
                            <v-textarea
                                v-model="note.text"
                                rows="1"
                                auto-grow
                                variant="plain"
                                hide-details
                                @blur="updateNote(note)"
                            ></v-textarea>
                        </td>
                        <td>
                            <span class="text-caption text-muted">
                                {{ note.turns_remaining }} / {{ note.turns_initial }} turns
                            </span>
                        </td>
                        <td>
                            <v-btn
                                icon="mdi-close-circle-outline"
                                size="x-small"
                                variant="text"
                                color="delete"
                                @click="removeNote(note.id)"
                            ></v-btn>
                        </td>
                    </tr>
                </tbody>
            </v-table>
            <v-alert v-else variant="text" density="compact" class="text-caption text-muted mb-3">
                No director notes yet.
            </v-alert>

            <!-- add note form -->
            <v-row dense align="center">
                <v-col>
                    <v-textarea
                        v-model="newNote.text"
                        label="New note"
                        rows="1"
                        auto-grow
                        hide-details
                    ></v-textarea>
                </v-col>
                <v-col cols="auto" style="min-width: 160px;">
                    <v-number-input
                        v-model="newNote.turns"
                        label="Turns"
                        hide-details
                        :min="1"
                        :max="999"
                    ></v-number-input>
                </v-col>
                <v-col cols="auto">
                    <v-btn
                        icon="mdi-plus"
                        size="small"
                        variant="tonal"
                        color="primary"
                        :disabled="!newNote.text || !newNote.turns"
                        @click="addNote()"
                    ></v-btn>
                </v-col>
            </v-row>
    </div>
</template>

<script>
export default {
    name: 'DirectorNotes',
    inject: [
        'getWebsocket',
        'registerMessageHandler',
        'unregisterMessageHandler',
    ],
    data() {
        return {
            notes: [],
            newNote: { text: '', turns: 10 },
        };
    },
    methods: {
        fetchNotes() {
            this.getWebsocket().send(JSON.stringify({
                type: 'director',
                action: 'notes_list',
            }));
        },

        addNote() {
            if (!this.newNote.text || !this.newNote.turns) return;
            this.getWebsocket().send(JSON.stringify({
                type: 'director',
                action: 'notes_add',
                text: this.newNote.text,
                turns: this.newNote.turns,
            }));
            this.newNote = { text: '', turns: 10 };
        },

        updateNote(note) {
            this.getWebsocket().send(JSON.stringify({
                type: 'director',
                action: 'notes_update',
                note_id: note.id,
                text: note.text,
            }));
        },

        removeNote(noteId) {
            this.getWebsocket().send(JSON.stringify({
                type: 'director',
                action: 'notes_remove',
                note_id: noteId,
            }));
        },

        handleMessage(message) {
            if (message.type === 'director' && message.action === 'notes_list') {
                this.notes = message.notes || [];
            }
        },
    },
    mounted() {
        this.registerMessageHandler(this.handleMessage);
        this.$nextTick(() => {
            this.fetchNotes();
        });
    },
    unmounted() {
        this.unregisterMessageHandler(this.handleMessage);
    },
};
</script>
