<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get, post } from '@/utils/request'
import ChatPanel from '@/components/ChatPanel.vue'
import { Modal } from '@arco-design/web-vue'
const route = useRoute(),
  token = String(route.params.token),
  app = ref<any>(),
  conversations = ref<any[]>([]),
  selected = ref(''),
  error = ref(''),
  rename = ref<any>(),
  name = ref(''),
  historyKey = ref(''),
  chatKey = ref(0),
  chat = ref<InstanceType<typeof ChatPanel>>()
async function list() {
  const [normal, pinned]: any[] = await Promise.all([
    get(`/web-apps/${token}/conversations`, { params: { is_pinned: '' } }),
    get(`/web-apps/${token}/conversations`, { params: { is_pinned: 'true' } }),
  ])
  conversations.value = [
    ...pinned.data.map((c: any) => ({ ...c, pinned: true })),
    ...normal.data.map((c: any) => ({ ...c, pinned: false })),
  ]
}
async function load() {
  error.value = ''
  try {
    app.value = (await get<any>(`/web-apps/${token}`)).data
    await list()
  } catch (e: any) {
    error.value = e.message
  }
}
function select(id: string) {
  chatKey.value += 1
  selected.value = id
  historyKey.value = id
}
async function pin(c: any) {
  await post(`/conversations/${c.id}/is-pinned`, { body: { is_pinned: !c.pinned } })
  await list()
}
async function remove(c: any) {
  await post(`/conversations/${c.id}/delete`)
  if (selected.value === c.id) select('')
  await list()
}
function confirmRemove(conversation: any) {
  Modal.warning({
    title: '删除会话？',
    content: '删除后无法恢复这段对话。',
    hideCancel: false,
    onOk: () => remove(conversation),
  })
}
function openRename(conversation: any) {
  rename.value = conversation
  name.value = conversation.name
}
async function saveName() {
  if (!name.value.trim()) return false
  await post(`/conversations/${rename.value.id}/name`, { body: { name: name.value } })
  rename.value = undefined
  await list()
}
onMounted(load)
</script>
<template>
  <div class="webchat">
    <aside>
      <router-link to="/home" class="brand"
        ><img src="/favicon.svg" alt="Youyou" /><span>Youyou <small>LLMOPS</small></span></router-link
      ><a-button long type="primary" :disabled="chat?.busy" @click="select('')"
        ><icon-plus /> 新对话</a-button
      >
      <p class="nav-section">历史会话</p>
      <div
        v-for="c in conversations"
        :key="c.id"
        class="conversation-row"
        :class="{ active: selected === c.id }"
      >
        <button :disabled="chat?.busy" @click="select(c.id)">
          {{ c.pinned ? '⌃ ' : '' }}{{ c.name || '新对话' }}</button
        ><a-dropdown
          ><a-button size="mini" type="text" :disabled="chat?.busy" aria-label="管理会话"
            ><icon-more /></a-button
          ><template #content
            ><a-doption @click="openRename(c)">重命名</a-doption
            ><a-doption @click="pin(c)">{{ c.pinned ? '取消置顶' : '置顶' }}</a-doption
            ><a-doption @click="confirmRemove(c)">删除</a-doption></template
          ></a-dropdown
        >
      </div>
      <footer>Built by Youyou</footer>
    </aside>
    <main>
      <header>
        <div>
          <h2>{{ app?.name || '应用对话' }}</h2>
          <p>{{ app?.description }}</p>
        </div>
        <router-link to="/home" class="text-link">工作台</router-link>
      </header>
      <div v-if="error" class="empty-state">
        <h2>无法访问此应用</h2>
        <p>{{ error }}</p>
        <a-button @click="load">重新加载</a-button>
      </div>
      <chat-panel
        v-else-if="app"
        ref="chat"
        :key="chatKey"
        :endpoint="`/web-apps/${token}/chat`"
        :history-endpoint="historyKey ? `/conversations/${historyKey}/messages` : ''"
        :conversation-id="selected"
        :stop-endpoint="(task) => `/web-apps/${token}/chat/${task}/stop`"
        :opening="app.app_config.opening_statement || `你好，我是 ${app.name}`"
        :questions="app.app_config.opening_questions"
        :suggestions="app.app_config.suggested_after_answer?.enable"
        :model-features="app.app_config.features"
        @conversation="selected = $event"
        @complete="list"
      /><a-spin v-else />
    </main>
    <a-modal
      :visible="!!rename"
      title="重命名会话"
      @cancel="rename = undefined"
      :on-before-ok="saveName"
      ><a-input v-model="name" aria-label="会话名称" :max-length="100"
    /></a-modal>
  </div>
</template>
<style scoped>
.webchat {
  height: 100dvh;
  display: flex;
}
.webchat aside {
  width: 250px;
  padding: 28px 16px;
  background: #f7f9f8;
  border-right: 1px solid #e3e9e5;
  display: flex;
  flex-direction: column;
  overflow: auto;
}
.webchat main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.webchat header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 28px;
  background: white;
  border-bottom: 1px solid #e5ebe7;
}
.webchat header p {
  font-size: 12px;
  color: #859389;
  margin-top: 6px;
}
.webchat :deep(.chat-panel) {
  flex: 1;
  min-height: 0;
}
.conversation-row {
  display: flex;
  align-items: center;
  padding: 6px;
  border-radius: 7px;
  margin-bottom: 3px;
}
.conversation-row.active {
  background: #e3efe8;
}
.conversation-row > button {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
  font-size: 12px;
  padding: 6px;
}
.webchat footer {
  margin-top: auto;
  padding-top: 30px;
  color: #96a198;
  font-size: 11px;
}
@media (max-width: 767px) {
  .webchat {
    flex-direction: column;
  }
  .webchat aside {
    width: 100%;
    max-height: 160px;
    padding: 10px 16px;
    flex-shrink: 0;
  }
  .webchat aside .brand,
  .webchat footer,
  .webchat .nav-section {
    display: none;
  }
  .webchat header {
    padding: 14px;
  }
  .webchat main {
    min-height: 0;
  }
  .conversation-row {
    flex-shrink: 0;
  }
}
</style>
