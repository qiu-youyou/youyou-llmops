<script setup lang="ts">
import { documentStatusLabel, isDocumentProcessing } from '@/utils/document-status'

defineProps<{
  status: string
  enabled?: boolean
  error?: string
}>()
</script>

<template>
  <a-tooltip v-if="status === 'error'" :content="error || '请检查文件内容'">
    <a-tag color="red">{{ documentStatusLabel(status, enabled) }}</a-tag>
  </a-tooltip>
  <a-tag v-else-if="isDocumentProcessing(status)" color="blue">
    <template #icon><icon-loading v-if="status !== 'waiting'" spin /></template>
    {{ documentStatusLabel(status, enabled) }}
  </a-tag>
  <a-tag v-else :color="status === 'completed' && enabled !== false ? 'green' : 'gray'">
    {{ documentStatusLabel(status, enabled) }}
  </a-tag>
</template>
