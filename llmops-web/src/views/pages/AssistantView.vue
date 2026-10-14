<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ChatPanel from '@/components/ChatPanel.vue'
import { get, post } from '@/utils/request'
const modelFeatures = ref<string[]>([])
onMounted(async () => {
  try {
    modelFeatures.value = (
      await get<{ data: { features: string[] } }>('/assistant-agent/capabilities')
    ).data.features
  } catch {
    modelFeatures.value = []
  }
})
const chat = ref<InstanceType<typeof ChatPanel>>()
async function clear() {
  await post('/assistant-agent/delete-conversation')
  await chat.value?.reload()
}
</script>
<template>
  <div class="assistant-page">
    <div class="assistant-heading">
      <div>
        <h1>AI 助手</h1>
        <p>一起探索想法，构建你的 AI 应用。</p>
      </div>
      <a-popconfirm content="清空当前助手对话？" @ok="clear"
        ><a-button :disabled="chat?.busy">清空对话</a-button></a-popconfirm
      >
    </div>
    <chat-panel
      ref="chat"
      endpoint="/assistant-agent/chat"
      history-endpoint="/assistant-agent/messages"
      :stop-endpoint="(task) => `/assistant-agent/chat/${task}/stop`"
      opening="你好，我是 Youyou AI 助手。"
      :questions="['帮我规划一个知识库问答应用', '如何设计一个工作流？', '介绍一下 RAG 的工作方式']"
      :suggestions="true"
      :model-features="modelFeatures"
    />
  </div>
</template>
<style scoped>
.assistant-page {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.assistant-heading {
  padding: 24px 32px;
  background: white;
  border-bottom: 1px solid #e8edeb;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.assistant-heading h1 {
  font-size: 20px;
}
.assistant-heading p {
  font-size: 12px;
  color: #89958e;
  margin-top: 6px;
}
.assistant-page :deep(.chat-panel) {
  flex: 1;
  min-height: 0;
}
</style>
