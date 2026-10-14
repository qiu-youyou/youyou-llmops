<script setup lang="ts">
import { ref, watch } from 'vue'
const props = defineProps<{ urls: string[] }>()
const failed = ref<Record<string, boolean>>({})
watch(
  () => props.urls,
  () => {
    failed.value = {}
  },
)
</script>
<template>
  <div v-if="urls.length" class="chat-images">
    <template v-for="(url, index) in urls" :key="`${index}:${url}`">
      <span v-if="failed[url]" class="image-unavailable" role="status">图片加载失败</span>
      <a-image
        v-else
        :src="url"
        :width="104"
        :height="104"
        :alt="`图片 ${index + 1}`"
        fit="cover"
        @error="failed[url] = true"
      />
    </template>
  </div>
</template>
<style scoped>
.chat-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0;
}
.image-unavailable {
  padding: 16px;
  font-size: 12px;
  color: #777;
}
</style>
