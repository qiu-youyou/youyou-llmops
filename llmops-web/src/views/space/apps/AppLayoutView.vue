<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getApp, publish, cancelPublish } from '@/services/app'
import { Message } from '@arco-design/web-vue'
import PublishHistoryDrawer from './components/PublishHistoryDrawer.vue'
import SaveStatus from '@/components/SaveStatus.vue'
const route = useRoute(),
  app = ref<any>({}),
  history = ref(false),
  loading = ref(false),
  revision = ref(0),
  error = ref('')
async function load() {
  try {
    app.value = (await getApp(String(route.params.app_id))).data
  } catch (e: any) {
    error.value = e.message
  }
}
async function publishApp() {
  loading.value = true
  try {
    await publish(app.value.id)
    await load()
    revision.value++
    Message.success('应用已发布')
  } finally {
    loading.value = false
  }
}
async function unpublish() {
  await cancelPublish(app.value.id)
  await load()
  revision.value++
  Message.success('已取消发布')
}
function restored() {
  revision.value++
  void load()
}
onMounted(load)
</script>
<template>
  <div class="app-layout">
    <header class="editor-header">
      <div class="editor-title">
        <router-link to="/space/apps" class="icon-button" aria-label="返回应用列表"
          ><icon-arrow-left /></router-link
        ><a-avatar shape="square" :image-url="app.icon">T</a-avatar>
        <div>
          <h2>{{ app.name || '加载应用…' }}</h2>
          <div class="flex gap-3 mt-1">
            <span class="status-badge">{{ app.status === 'published' ? '已发布' : '草稿' }}</span
            ><save-status />
          </div>
        </div>
      </div>
      <nav class="editor-tabs">
        <router-link :to="`/space/apps/${route.params.app_id}`">编排</router-link
        ><router-link :to="`/space/apps/${route.params.app_id}/published`">发布</router-link
        ><router-link :to="`/space/apps/${route.params.app_id}/analysis`">分析</router-link>
      </nav>
      <div class="editor-actions">
        <a-button @click="history = true">发布历史</a-button
        ><a-popconfirm
          v-if="app.status === 'published'"
          content="取消发布后，分享链接将无法使用。"
          @ok="unpublish"
          ><a-button>取消发布</a-button></a-popconfirm
        ><a-button type="primary" :loading="loading" :disabled="!app.id" @click="publishApp">{{
          app.status === 'published' ? '更新发布' : '发布应用'
        }}</a-button>
      </div>
    </header>
    <div v-if="error" class="error-panel">{{ error }}<a-button @click="load">重试</a-button></div>
    <router-view
      v-else-if="app.id"
      :key="route.path + revision"
      :app="app"
    /><publish-history-drawer
      :app="app"
      v-model:visible="history"
      @load-draft-app-config="restored"
    />
  </div>
</template>
<style scoped>
.app-layout {
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: auto;
}
.editor-header {
  flex-shrink: 0;
}
</style>
