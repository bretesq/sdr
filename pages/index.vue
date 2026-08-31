<template>
  <main class="p-4">
    <header class="mb-4">
      <h1 class="text-3xl font-bold m-0">SDR Console</h1>
      <p class="text-color-secondary mt-1 mb-0">
        Baton Rouge LWIN P25 — recording, playback and talkgroup reference
      </p>
    </header>

    <div class="grid">
      <div class="col-12 lg:col-4">
        <ListenControl />
      </div>
      <div class="col-12 lg:col-8">
        <RecordingsList />
      </div>
      <div class="col-12">
        <TalkgroupBrowser />
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
useHead({ title: 'SDR Console — LWIN P25' })
</script>
