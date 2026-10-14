<script setup lang="ts">
import { ref, watch } from 'vue'
import { getLanguageModel } from '@/services/language-model'
import { post } from '@/utils/request'
import { useRoute } from 'vue-router'
import { useGetDraftAppConfig } from '@/hooks/use-app'
import PresetPromptTextarea from './components/PresetPromptTextarea.vue'
import PreviewDebugHeader from './components/PreviewDebugHeader.vue'
import AgentAppAbility from './components/AgentAppAbility.vue'
import ModelConfig from './components/ModelConfig.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import WorkflowAbility from './components/WorkflowAbility.vue'
const route = useRoute(),
  id = String(route.params.app_id)
defineProps<{ app: Record<string, any> }>()
const chat = ref<InstanceType<typeof ChatPanel>>()
async function clearChat() {
  await post(`/apps/${id}/conversations/delete-debug-conversation`)
  await chat.value?.reload()
}
const { draftAppConfigForm } = useGetDraftAppConfig(id)
const modelFeatures = ref<string[]>([])
watch(
  () => [
    draftAppConfigForm.value.model_config?.provider,
    draftAppConfigForm.value.model_config?.model,
  ],
  async ([provider, model], _, onCleanup) => {
    let stale = false
    onCleanup(() => {
      stale = true
    })
    modelFeatures.value = []
    if (!provider || !model) return
    try {
      const result = await getLanguageModel(provider, model)
      if (!stale) modelFeatures.value = result.data.features
    } catch {
      if (!stale) modelFeatures.value = []
    }
  },
  { immediate: true },
)
</script>
<template>
  <div v-if="draftAppConfigForm.model_config" class="app-editor">
    <div class="app-config">
      <section class="config-section">
        <div class="config-section-title">
          <h2>模型与上下文</h2>
          <span class="eyebrow">01 / MODEL</span>
        </div>
        <model-config
          :dialog_round="draftAppConfigForm.dialog_round"
          v-model:model_config="draftAppConfigForm.model_config"
          :app_id="id"
        />
      </section>
      <section class="config-section prompt-section">
        <div class="config-section-title">
          <h2>角色与任务</h2>
          <span class="eyebrow">02 / INSTRUCTIONS</span>
        </div>
        <preset-prompt-textarea
          v-model:preset_prompt="draftAppConfigForm.preset_prompt"
          :app_id="id"
        />
      </section>
      <section class="config-section">
        <div class="config-section-title">
          <h2>能力与行为</h2>
          <span class="eyebrow">03 / CAPABILITIES</span>
        </div>
        <workflow-ability :app-id="id" v-model="draftAppConfigForm.workflows" /><agent-app-ability
          :draft_app_config="draftAppConfigForm"
          :app_id="id"
        />
      </section>
    </div>
    <div class="app-preview">
      <preview-debug-header :app_id="id" :long_term_memory="draftAppConfigForm.long_term_memory" />
      <div class="px-4 py-2 text-right">
        <a-popconfirm content="清空调试会话？" @ok="clearChat"
          ><a-button size="mini" type="text" :disabled="chat?.busy"
            >清空调试对话</a-button
          ></a-popconfirm
        >
      </div>
      <chat-panel
        ref="chat"
        :endpoint="`/apps/${id}/conversations`"
        :history-endpoint="`/apps/${id}/conversations/messages`"
        :stop-endpoint="(task) => `/apps/${id}/conversations/tasks/${task}/stop`"
        :opening="draftAppConfigForm.opening_statement || '调试你的应用'"
        :questions="draftAppConfigForm.opening_questions"
        :suggestions="draftAppConfigForm.suggested_after_answer.enable"
        :model-features="modelFeatures"
        draft
      />
    </div>
  </div>
</template>
<style scoped>
.app-preview {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.app-preview > :not(.chat-panel) {
  flex-shrink: 0;
}
.app-preview :deep(.chat-panel) {
  flex: 1;
  min-height: 0;
}
.prompt-section :deep(textarea) {
  min-height: 200px !important;
}
.prompt-section :deep(.h-full) {
  height: auto;
}
.app-config :deep(.h-\[calc\(100vh-141px\)\]) {
  height: auto;
}
.app-config :deep(.overflow-scroll) {
  overflow: visible;
}
@media (max-width: 767px) {
  .app-preview {
    height: min(650px, 100dvh);
  }
}
</style>
