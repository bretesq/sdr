<template>
  <div class="card p-3 border-round surface-card">
    <h2 class="text-xl font-semibold mt-0 mb-3">Scanner Feed</h2>

    <Message v-if="error" severity="error" :closable="false" class="mb-2">
      {{ error }}
    </Message>

    <div class="mb-3">
      <label for="feed-tgs" class="block mb-1 text-sm text-color-secondary">
        Talkgroups ({{ followed.length }} followed)
      </label>
      <MultiSelect
        id="feed-tgs"
        v-model="selected"
        :options="followed"
        option-label="label"
        option-value="tgid"
        filter
        display="chip"
        placeholder="Select talkgroups"
        class="w-full"
      />
    </div>

    <div class="flex align-items-center gap-2">
      <Button
        :label="armed ? 'Stop' : 'Play'"
        :icon="armed ? 'pi pi-stop' : 'pi pi-play'"
        :disabled="!armed && selected.length === 0"
        @click="armed ? disarm() : arm()"
      />
      <span v-if="nowPlaying" class="font-medium">
        {{ nowPlaying.alpha ?? nowPlaying.tgid }}
      </span>
      <span v-else-if="armed" class="text-color-secondary">waiting…</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'

const feed = useScannerFeed()
const {
  selected, armed, nowPlaying, error, arm, disarm,
} = feed

// MultiSelect needs a flat label; keep the activity count visible in it so the
// ranking from the server is legible in the dropdown.
const followed = computed(() =>
  feed.followed.value.map(t => ({
    ...t,
    label: `${t.tgid} · ${t.alpha ?? 'unknown'} (${t.recentCalls})`,
  })),
)

onMounted(feed.load)
</script>
