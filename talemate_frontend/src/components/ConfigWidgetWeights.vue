<template>
    <div class="mt-3">
        <div class="text-caption text-muted" v-if="label">
            <strong>{{ label }}</strong>
        </div>
        <div class="text-caption text-grey mb-2" v-if="description">
            {{ description }}
        </div>
        <div v-for="choice in choices" :key="choice.value" class="d-flex align-center">
            <div class="weight-label text-caption">
                {{ choice.label }}
                <v-icon
                    v-if="lastTouched === choice.value"
                    size="x-small"
                    color="muted"
                    icon="mdi-pin"
                    class="ml-1"
                ></v-icon>
            </div>
            <v-slider
                :model-value="weightFor(choice.value)"
                @update:modelValue="onSlide(choice.value, $event)"
                @end="lastTouched = choice.value"
                :min="0"
                :max="1"
                :step="step"
                density="compact"
                color="primary"
                hide-details
                thumb-label="always"
                class="flex-grow-1"
            >
                <template v-slot:thumb-label="{ modelValue }">
                    {{ formatWeight(modelValue) }}
                </template>
            </v-slider>
        </div>
    </div>
</template>

<script>
export default {
    props: {
        modelValue: {
            type: Object,
            required: true,
        },
        choices: {
            type: Array,
            required: true,
        },
        label: {
            type: String,
            required: false,
        },
        description: {
            type: String,
            required: false,
        },
        step: {
            type: Number,
            required: false,
            default: 0.05,
        },
    },
    emits: ['update:modelValue'],
    data() {
        return {
            lastTouched: null,
        };
    },
    methods: {
        weightFor(key) {
            const v = this.modelValue?.[key];
            return typeof v === 'number' ? v : 0;
        },
        formatWeight(v) {
            const precision = Math.max(0, Math.round(-Math.log10(this.step)));
            return Number(v).toFixed(precision);
        },
        onSlide(changedKey, newValue) {
            const keys = this.choices.map(c => c.value);
            const current = {};
            keys.forEach(k => { current[k] = this.weightFor(k); });

            // Pin the previously-released slider so it doesn't drift while the
            // user adjusts a different one. With fewer than 3 keys there's no
            // free slider to absorb the remainder, so we can't honor the lock.
            let lockedKey = (this.lastTouched && this.lastTouched !== changedKey)
                ? this.lastTouched
                : null;
            if (lockedKey && keys.length < 3) {
                lockedKey = null;
            }
            const lockedValue = lockedKey ? current[lockedKey] : 0;

            const clamped = Math.max(0, Math.min(1 - lockedValue, newValue));
            const freeKeys = keys.filter(k => k !== changedKey && k !== lockedKey);
            const freeBudget = Math.max(0, 1 - clamped - lockedValue);
            const freeSum = freeKeys.reduce((acc, k) => acc + current[k], 0);

            const next = { [changedKey]: clamped };
            if (lockedKey) next[lockedKey] = lockedValue;

            if (freeKeys.length > 0) {
                if (freeSum <= 0) {
                    const share = freeBudget / freeKeys.length;
                    freeKeys.forEach(k => { next[k] = share; });
                } else {
                    freeKeys.forEach(k => { next[k] = (current[k] / freeSum) * freeBudget; });
                }
            }

            // Round to step precision. Absorb sub-step rounding drift on the
            // largest non-locked key so the lock stays put and the changed
            // slider isn't pushed past its clip ceiling.
            const rounded = {};
            const precision = Math.max(0, Math.round(-Math.log10(this.step)));
            keys.forEach(k => { rounded[k] = parseFloat(next[k].toFixed(precision + 2)); });
            const drift = 1 - Object.values(rounded).reduce((a, b) => a + b, 0);
            const absorbCandidates = keys.filter(k => k !== lockedKey);
            const driftKey = absorbCandidates.reduce(
                (best, k) => (rounded[k] > rounded[best] ? k : best),
                absorbCandidates[0],
            );
            rounded[driftKey] = parseFloat((rounded[driftKey] + drift).toFixed(precision + 2));

            this.$emit('update:modelValue', rounded);
        },
    },
};
</script>

<style scoped>
.weight-label {
    width: 180px;
    flex-shrink: 0;
    padding-right: 12px;
}
</style>
