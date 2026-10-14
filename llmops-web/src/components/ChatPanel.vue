<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { get, post, ssePost } from '@/utils/request'
import { applyChatFrame, usageLabel, type ChatMessage } from '@/utils/chat'
import { appDraftQueueFor } from '@/utils/draft-queue'
import { generateSuggestedQuestions } from '@/services/ai'
import ChatImages from '@/components/ChatImages.vue'
import { ModelFeature } from '@/models/language-model'
import {
  CHAT_IMAGE_TYPES,
  MAX_IMAGE_FILE_SIZE,
  MAX_MESSAGE_IMAGES,
  type ChatRequest,
} from '@/models/chat'
import { uploadImage } from '@/services/upload-file'
import MarkdownContent from '@/components/MarkdownContent.vue'
const props = withDefaults(
  defineProps<{
    endpoint: string
    historyEndpoint: string
    stopEndpoint: (task: string) => string
    conversationId?: string
    opening?: string
    questions?: string[]
    suggestions?: boolean
    draft?: boolean
    modelFeatures?: string[]
  }>(),
  { opening: '有什么想法？从一个问题开始。', questions: () => [], modelFeatures: () => [] },
)
const emit = defineEmits<{ conversation: [id: string]; complete: [] }>()
const messages = ref<ChatMessage[]>([]),
  query = ref(''),
  busy = ref(false),
  error = ref(''),
  historyBusy = ref(false),
  more = ref(false),
  suggested = ref<string[]>([]),
  scroll = ref<HTMLElement>(),
  page = ref(1)
interface PendingImage {
  id: number
  file: File
  preview: string
  url?: string
  uploading: boolean
  error?: string
}
const pendingImages = ref<PendingImage[]>([])
const fileInput = ref<HTMLInputElement>()
let nextImageId = 0
let disposed = false
const supportsImages = computed(() => props.modelFeatures.includes(ModelFeature.ImageInput))
const imagesReady = computed(() =>
  pendingImages.value.every((image) => image.url && !image.uploading && !image.error),
)
function canSendMessage(text: string): boolean {
  if (busy.value || (!text.trim() && pendingImages.value.length === 0)) return false
  return pendingImages.value.length === 0 || (supportsImages.value && imagesReady.value)
}
const canSend = computed(() => canSendMessage(query.value))
async function uploadPending(item: PendingImage) {
  item.uploading = true
  item.error = undefined
  try {
    const result = await uploadImage(item.file)
    const url = result.data.image_url
    if (!url.startsWith('https://')) throw new Error('图片地址需要使用 HTTPS，请检查上传服务配置')
    item.url = url
  } catch (e: any) {
    item.error = e.message || '图片上传失败'
  } finally {
    item.uploading = false
  }
}
function selectImages(event: Event) {
  const input = event.target as HTMLInputElement
  if (!supportsImages.value || busy.value) {
    input.value = ''
    return
  }
  for (const file of Array.from(input.files || [])) {
    if (pendingImages.value.length >= MAX_MESSAGE_IMAGES) {
      error.value = `每条消息最多${MAX_MESSAGE_IMAGES}张图片`
      break
    }
    if (!CHAT_IMAGE_TYPES.includes(file.type) || file.size > MAX_IMAGE_FILE_SIZE) {
      error.value = '仅支持15 MB以内的 JPEG、PNG、WebP 图片'
      continue
    }
    pendingImages.value.push({
      id: ++nextImageId,
      file,
      preview: URL.createObjectURL(file),
      uploading: true,
    })
    void uploadPending(pendingImages.value[pendingImages.value.length - 1])
  }
  input.value = ''
}
function removeImage(id: number) {
  const item = pendingImages.value.find((i) => i.id === id)
  if (item) URL.revokeObjectURL(item.preview)
  pendingImages.value = pendingImages.value.filter((i) => i.id !== id)
}
let controller: AbortController | undefined
async function load(reset = true) {
  if (!props.historyEndpoint) return
  historyBusy.value = true
  error.value = ''
  try {
    if (reset) page.value = 1
    const res: any = await get(props.historyEndpoint, {
      params: {
        current_page: page.value,
        page_size: 20,
        created_at: reset ? undefined : messages.value[0]?.created_at,
      },
    })
    const list = (res.data.list || []).slice().reverse()
    messages.value = reset ? list : [...list, ...messages.value]
    more.value = res.data.paginator?.total_page > page.value
    page.value++
    if (reset) await bottom()
  } catch (e: any) {
    error.value = e.message
  } finally {
    historyBusy.value = false
  }
}
async function bottom() {
  await nextTick()
  scroll.value?.scrollTo({ top: scroll.value.scrollHeight, behavior: 'smooth' })
}
async function send(text = query.value) {
  if (!canSendMessage(text)) return
  const attachments = pendingImages.value.slice()
  const imageUrls = attachments.map((image) => image.url!)
  const req: ChatRequest = { query: text, image_urls: imageUrls }
  if (props.conversationId) req.conversation_id = props.conversationId
  busy.value = true
  error.value = ''
  suggested.value = []
  controller = new AbortController()
  const index = messages.value.length
  messages.value.push({
    id: '',
    query: text,
    image_urls: imageUrls,
    answer: '',
    agent_thoughts: [],
    status: 'running',
  })
  query.value = ''
  pendingImages.value = []
  await bottom()
  try {
    if (props.draft) await appDraftQueueFor(props.endpoint.split('/')[2]).flush()
    await ssePost(
      props.endpoint,
      {
        body: req,
        signal: controller.signal,
      },
      (frame) => {
        applyChatFrame(messages.value[index], frame)
        const id = messages.value[index].conversation_id
        if (id) emit('conversation', id)
        void bottom()
      },
    )
    const msg = messages.value[index]
    if (props.suggestions && msg.status === 'agent_end' && msg.id) {
      try {
        const res: any = await generateSuggestedQuestions(msg.id)
        suggested.value = res.data.questions || res.data || []
      } catch {
        /* answer remains usable */
      }
    }
    emit('complete')
  } catch (e: any) {
    messages.value[index].error = e.message
    messages.value[index].status = 'error'
  } finally {
    if (!disposed && ['error', 'timeout'].includes(messages.value[index].status || '')) {
      query.value = text
      pendingImages.value = attachments
    } else {
      attachments.forEach((i) => URL.revokeObjectURL(i.preview))
    }
    busy.value = false
    await bottom()
  }
}
async function stop() {
  const task = messages.value.at(-1)?.task_id
  if (task) {
    try {
      await post(props.stopEndpoint(task))
    } catch (e: any) {
      error.value = e.message
    }
  } else controller?.abort()
}
onMounted(() => load())
watch(
  () => props.historyEndpoint,
  () => {
    if (!busy.value) {
      messages.value = []
      void load()
    }
  },
)
onBeforeUnmount(() => {
  disposed = true
  controller?.abort()
  pendingImages.value.forEach((i) => URL.revokeObjectURL(i.preview))
})
defineExpose({ reload: () => load(), busy })
</script>
<template>
  <section class="chat-panel">
    <div ref="scroll" class="chat-scroll">
      <div v-if="error" class="error-panel">
        {{ error }}<a-button @click="load()">重试</a-button>
      </div>
      <div class="text-center">
        <a-button v-if="more" size="small" :loading="historyBusy" @click="load(false)"
          >加载更早消息</a-button
        ><a-spin v-else-if="historyBusy" />
      </div>
      <div v-if="!messages.length && !historyBusy" class="chat-welcome">
        <img src="/favicon.svg" alt="Youyou" />
        <h2>{{ opening }}</h2>
        <p>清晰表达任务，让 AI 帮你迈出下一步。</p>
        <button v-for="question in questions" :key="question" @click="send(question)">
          {{ question }} <icon-arrow-right />
        </button>
      </div>
      <article v-for="(message, i) in messages" :key="message.id || i" class="chat-turn">
        <div class="human-bubble">
          {{ message.query }}<ChatImages :urls="message.image_urls || []" />
        </div>
        <div class="assistant-answer">
          <div class="answer-label">
            <img src="/favicon.svg" alt="" />Youyou
            <span v-if="message.status === 'running'">正在运行…</span>
          </div>
          <MarkdownContent :content="message.answer" />
          <details v-if="message.agent_thoughts?.length">
            <summary>运行过程 · {{ message.agent_thoughts.length }} 个步骤</summary>
            <div
              v-for="(step, n) in message.agent_thoughts"
              :key="step.id || n"
              class="thought-step"
            >
              <strong>{{ step.tool || step.event }}</strong>
              <pre>{{ step.thought || step.observation }}</pre>
              <small v-if="step.usage">{{ usageLabel(step.usage) }}</small>
            </div>
          </details>
          <p v-if="message.error" class="chat-error" role="alert">{{ message.error }}</p>
          <small class="usage-label"
            >{{ usageLabel(message.usage)
            }}{{ message.status === 'stop' ? ' · 已停止' : '' }}</small
          >
        </div>
      </article>
    </div>
    <div class="chat-composer">
      <div v-if="suggested.length" class="suggestions">
        <button v-for="q in suggested" :key="q" @click="send(q)">{{ q }}</button>
      </div>
      <form @submit.prevent="send()">
        <div v-if="pendingImages.length" class="pending-images">
          <div v-for="item in pendingImages" :key="item.id" class="pending-image">
            <img :src="item.preview" alt="待发送图片" />
            <span v-if="item.uploading">上传中…</span>
            <span v-if="item.error" role="alert">{{ item.error }}</span>
            <button v-if="item.error" type="button" @click="uploadPending(item)">重试上传</button>
            <button type="button" :disabled="busy" @click="removeImage(item.id)">移除</button>
          </div>
        </div>
        <p v-if="pendingImages.length && !supportsImages" role="alert">
          当前模型不支持图片或能力尚未加载，请移除图片或切换模型。
        </p>
        <input
          ref="fileInput"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          multiple
          hidden
          @change="selectImages"
        />
        <a-button
          type="text"
          :disabled="!supportsImages || busy || pendingImages.length >= MAX_MESSAGE_IMAGES"
          @click="fileInput?.click()"
          >添加图片</a-button
        >
        <textarea
          v-model="query"
          aria-label="输入消息"
          placeholder="输入消息，Enter 发送，Shift + Enter 换行"
          :disabled="busy"
          rows="2"
          @keydown.enter="
            (e: KeyboardEvent) => {
              if (!e.shiftKey && !e.isComposing) {
                e.preventDefault()
                send()
              }
            }
          "
        />
        <div class="composer-footer">
          <small>{{ busy ? '正在生成，你可以随时停止' : '回答由 AI 生成，请核实重要信息' }}</small
          ><a-button v-if="busy" @click="stop">停止</a-button
          ><a-button v-else type="primary" html-type="submit" :disabled="!canSend"
            ><icon-arrow-up
          /></a-button>
        </div>
      </form>
    </div>
  </section>
</template>
<style scoped>
.pending-images {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.pending-image {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 104px;
  font-size: 12px;
}
.pending-image img {
  width: 104px;
  height: 80px;
  object-fit: cover;
  border-radius: 6px;
}
.pending-image button {
  color: #49725a;
}

.chat-panel {
  height: 100%;
  min-height: 480px;
  display: flex;
  flex-direction: column;
  background: #fff;
}
.chat-scroll {
  flex: 1;
  overflow: auto;
  padding: 24px;
  min-height: 0;
}
.chat-welcome {
  max-width: 440px;
  margin: 60px auto;
  text-align: center;
}
.chat-welcome > img {
  width: 46px;
  margin: 0 auto 24px;
}
.chat-welcome h2 {
  font-size: 20px;
  line-height: 1.7;
}
.chat-welcome p {
  color: #94a09a;
  margin: 12px 0 24px;
  font-size: 12px;
}
.chat-welcome button {
  display: flex;
  justify-content: space-between;
  align-items: center;
  text-align: left;
  border: 1px solid #e5ece8;
  border-radius: 8px;
  padding: 12px 16px;
  width: 100%;
  margin-bottom: 10px;
  color: #597367;
}
.chat-turn {
  max-width: 800px;
  margin: 0 auto 28px;
}
.human-bubble {
  background: #eef4f1;
  border-radius: 12px 12px 2px 12px;
  padding: 14px 18px;
  width: fit-content;
  max-width: 90%;
  margin-left: auto;
  white-space: pre-wrap;
}
.assistant-answer {
  margin-top: 22px;
}
.answer-label {
  display: flex;
  gap: 8px;
  align-items: center;
  letter-spacing: 1px;
  font-size: 10px;
  color: #7b8c83;
  margin-bottom: 12px;
}
.answer-label img {
  width: 22px;
}
.answer-label span {
  margin-left: auto;
  letter-spacing: 0;
}
.usage-label {
  display: block;
  color: #98a29c;
  font-size: 10px;
  margin-top: 12px;
}
details {
  margin-top: 12px;
  font-size: 12px;
  color: #7a8780;
}
summary {
  cursor: pointer;
}
.thought-step {
  border-left: 2px solid #d9e9e0;
  margin: 10px 0;
  padding: 8px 12px;
}
.thought-step pre {
  font-size: 11px;
  max-height: 220px;
  overflow: auto;
}
.chat-error {
  color: #b43c3c;
  background: #fff3f1;
  padding: 12px;
  border-radius: 6px;
  margin-top: 10px;
}
.chat-composer {
  padding: 16px 24px 22px;
  border-top: 1px solid #f2f3f4;
}
.chat-composer form {
  border: 1px solid #dae4de;
  border-radius: 12px;
  padding: 12px;
  max-width: 800px;
  margin: auto;
  box-shadow: 0 4px 16px #172f2005;
}
.chat-composer textarea {
  width: 100%;
  resize: vertical;
  background: transparent;
  outline: none;
  min-height: 48px;
  max-height: 160px;
}
.composer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.composer-footer small {
  color: #9ba59f;
  font-size: 10px;
}
.suggestions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.suggestions button {
  font-size: 11px;
  padding: 6px 10px;
  border: 1px solid #dce8e0;
  border-radius: 6px;
  color: #49725a;
}
@media (max-width: 767px) {
  .chat-scroll {
    padding: 16px;
  }
  .chat-composer {
    padding: 12px;
  }
  .chat-welcome {
    margin: 25px auto;
  }
}
</style>
