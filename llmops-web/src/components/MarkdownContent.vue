<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import 'highlight.js/styles/github-dark.css'

const props = defineProps<{ content: string }>()
const html = computed(() => renderMarkdown(props.content))
</script>

<template>
  <!-- 仅渲染禁用原始 HTML、保留默认链接协议校验的 markdown-it 输出。 -->
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="markdown-content" v-html="html" />
</template>

<style scoped>
.markdown-content {
  min-width: 0;
  max-width: 100%;
  line-height: 1.8;
  white-space: normal;
  word-break: normal;
  overflow-wrap: anywhere;
}
.markdown-content :deep(p),
.markdown-content :deep(ul),
.markdown-content :deep(ol),
.markdown-content :deep(blockquote),
.markdown-content :deep(pre),
.markdown-content :deep(table) {
  margin: 0.75em 0;
}
.markdown-content :deep(> :first-child) {
  margin-top: 0;
}
.markdown-content :deep(> :last-child) {
  margin-bottom: 0;
}
.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4),
.markdown-content :deep(h5),
.markdown-content :deep(h6) {
  margin: 1em 0 0.5em;
  font-weight: 600;
  line-height: 1.4;
}
.markdown-content :deep(h1) {
  font-size: 1.5em;
}
.markdown-content :deep(h2) {
  font-size: 1.3em;
}
.markdown-content :deep(h3) {
  font-size: 1.15em;
}
.markdown-content :deep(ul) {
  list-style: disc;
  padding-left: 1.5em;
}
.markdown-content :deep(ol) {
  list-style: decimal;
  padding-left: 1.5em;
}
.markdown-content :deep(blockquote) {
  border-left: 3px solid #cbd5e1;
  padding-left: 1em;
  color: #64748b;
}
.markdown-content :deep(a) {
  color: #2563eb;
  text-decoration: underline;
}
.markdown-content :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.9em;
  background: #e2e8f0;
  border-radius: 4px;
  padding: 0.15em 0.3em;
}
.markdown-content :deep(pre) {
  overflow-x: auto;
  padding: 1em;
  border-radius: 8px;
  background: #0d1117;
  color: #c9d1d9;
  white-space: pre;
  overflow-wrap: normal;
}
.markdown-content :deep(pre code) {
  background: none;
  padding: 0;
}
.markdown-content :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}
.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid #cbd5e1;
  padding: 0.4em 0.75em;
}
.markdown-content :deep(th) {
  font-weight: 600;
  background: #f1f5f9;
}
.markdown-content :deep(img) {
  max-width: 100%;
  height: auto;
}
.markdown-content :deep(hr) {
  margin: 1em 0;
  border-top: 1px solid #cbd5e1;
}
</style>
