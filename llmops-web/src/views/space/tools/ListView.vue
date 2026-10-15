<script setup lang="ts">
import { uploadAdapter } from '@/utils/upload-adapter'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  createApiToolProvider,
  deleteApiToolProvider,
  getApiToolProvider,
  updateApiToolProvider,
  validateOpenAPISchema,
} from '@/services/api-tool'
import { useGetApiToolProvidersWithPage } from '@/hooks/use-tool'
import { parseApiToolSchema } from '@/utils/api-tool-schema'
import { uploadImage } from '@/services/upload-file'
import type { CreateApiToolProviderRequest } from '@/models/api-tool'
import moment from 'moment/moment'
import { typeMap } from '@/config'
import { Form, Message, Modal, type ValidatedError } from '@arco-design/web-vue'

const route = useRoute()
const router = useRouter()
const props = defineProps({ createType: { type: String, required: true } })
const emits = defineEmits(['update-create-type'])
const {
  api_tool_providers: providers,
  paginator,
  loading,
  error: loadError,
  loadApiToolProviders,
} = useGetApiToolProvidersWithPage()
const emptyForm = () => ({
  fileList: [] as any[],
  icon: '',
  name: '',
  openapi_schema: '',
  headers: [] as { key: string; value: string }[],
})
const form = reactive(emptyForm())
const formRef = ref<InstanceType<typeof Form>>()
const selectedProviderId = ref('')
const selectedProvider = computed(() =>
  providers.find((item) => item.id === selectedProviderId.value),
)
const editingProviderId = ref('')
const showUpdateModal = ref(false)
const showUpdateModalLoading = ref(false)
const submitLoading = ref(false)
const schemaError = ref('')
const validatedSchema = ref('')
const tools = computed(() => parseApiToolSchema(form.openapi_schema).tools)

const showSchemaGuide = ref(false)
const schemaExampleType = ref<'get' | 'post'>('get')
// 示例使用与后端一致的简化格式，仅供复制，不写入用户表单。
const schemaExamples = {
  get: {
    server: 'https://api.example.com',
    description: '查询商品信息（请替换为实际服务地址）。',
    paths: {
      '/products': {
        get: {
          operationId: 'search_products',
          description: '根据关键词搜索商品。',
          parameters: [
            { name: 'query', in: 'query', description: '商品关键词', required: true, type: 'str' },
            {
              name: 'limit',
              in: 'query',
              description: '返回数量，可省略',
              required: false,
              type: 'int',
            },
          ],
        },
      },
    },
  },
  post: {
    server: 'https://api.bocha.cn',
    description: '博查联网搜索，返回网页标题、链接和摘要。',
    paths: {
      '/v1/web-search': {
        post: {
          operationId: 'bocha_web_search',
          description: '搜索实时网页信息。通常传入 summary=true、count=5、freshness=noLimit。',
          parameters: [
            {
              name: 'query',
              in: 'request_body',
              description: '搜索关键词或问题',
              required: true,
              type: 'str',
            },
            {
              name: 'summary',
              in: 'request_body',
              description: '是否返回较长摘要，通常为 true',
              required: true,
              type: 'bool',
            },
            {
              name: 'count',
              in: 'request_body',
              description: '结果数量，1 到 50，通常为 5',
              required: true,
              type: 'int',
            },
            {
              name: 'freshness',
              in: 'request_body',
              description: 'noLimit、oneDay、oneWeek、oneMonth 或 oneYear',
              required: true,
              type: 'str',
            },
          ],
        },
      },
    },
  },
}
const schemaExample = computed(() =>
  JSON.stringify(schemaExamples[schemaExampleType.value], null, 2),
)
const copySchemaExample = async () => {
  try {
    await navigator.clipboard.writeText(schemaExample.value)
    Message.success('示例已复制，可粘贴到 Schema 输入框后修改')
  } catch {
    Message.warning('无法访问剪贴板，请选中下方示例手动复制')
  }
}

const resetForm = () => {
  formRef.value?.resetFields()
  Object.assign(form, emptyForm())
  schemaError.value = ''
  validatedSchema.value = ''
  editingProviderId.value = ''
  showSchemaGuide.value = false
  schemaExampleType.value = 'get'
}
const loadMoreData = () => loadApiToolProviders(false, String(route.query.search_word ?? ''))
const initData = () => {
  selectedProviderId.value = ''
  return loadApiToolProviders(true, String(route.query.search_word ?? ''))
}
const handleScroll = (event: UIEvent) => {
  const { scrollTop, scrollHeight, clientHeight } = event.target as HTMLElement
  if (scrollTop + clientHeight >= scrollHeight - 10) void loadMoreData()
}

// 校验结果只应用于当前 Schema，完整规范由后端决定。
const validateSchema = async (source = form.openapi_schema) => {
  if (!source.trim()) return false
  if (validatedSchema.value === source) return true
  try {
    await validateOpenAPISchema(source)
    if (form.openapi_schema === source) {
      validatedSchema.value = source
      schemaError.value = ''
    }
    return true
  } catch (error) {
    if (form.openapi_schema === source)
      schemaError.value = error instanceof Error ? error.message : 'Schema 校验失败'
    return false
  }
}

const handleUpdate = async () => {
  const provider = selectedProvider.value
  if (!provider || showUpdateModalLoading.value) return
  showUpdateModalLoading.value = true
  try {
    const { data } = await getApiToolProvider(provider.id)
    if (selectedProviderId.value !== provider.id || props.createType === 'tool') return
    resetForm()
    form.fileList = [{ uid: '1', name: '插件图标', url: data.icon }]
    form.icon = data.icon
    form.name = data.name
    form.openapi_schema = data.openapi_schema
    form.headers = (data.headers ?? []).map((header) => ({ ...header }))
    editingProviderId.value = provider.id
    showUpdateModal.value = true
  } catch {
    // 请求层已显示错误，不打开空的编辑表单。
  } finally {
    showUpdateModalLoading.value = false
  }
}

const handleCancel = () => {
  if (submitLoading.value) return
  resetForm()
  emits('update-create-type', '')
  showUpdateModal.value = false
  if (route.query.create === '1') {
    const { create, ...query } = route.query
    void router.replace({ path: route.path, query })
  }
}

const handleDelete = () => {
  const providerId = editingProviderId.value
  if (!providerId) return
  Modal.warning({
    title: '删除这个工具?',
    content: '删除工具是不可逆的。AI应用将无法再访问您的工具',
    hideCancel: false,
    onOk: async () => {
      try {
        const resp = await deleteApiToolProvider(providerId)
        Message.success(resp.message)
        handleCancel()
        await initData()
        return true
      } catch {
        // 删除失败保留详情和表单，允许重试。
        return false
      }
    },
  })
}

const handleSubmit = async ({
  errors,
}: {
  values: Record<string, any>
  errors: Record<string, ValidatedError> | undefined
}) => {
  if (errors || submitLoading.value) return
  const providerId = editingProviderId.value
  if (props.createType !== 'tool' && !providerId) return
  // 明确构造接口请求，不发送 fileList 等 UI 状态。
  const request: CreateApiToolProviderRequest = {
    name: form.name.trim(),
    icon: form.icon,
    openapi_schema: form.openapi_schema,
    headers: form.headers.map(({ key, value }) => ({ key: key.trim(), value })),
  }
  submitLoading.value = true
  let saved = false
  try {
    if (!(await validateSchema(request.openapi_schema))) return
    const resp = providerId
      ? await updateApiToolProvider(providerId, request)
      : await createApiToolProvider(request)
    Message.success(resp.message)
    saved = true
  } catch {
    // 接口已提示失败，保留输入用于修改或重试。
  } finally {
    submitLoading.value = false
  }
  if (saved) {
    handleCancel()
    await initData()
  }
}

watch(
  () => props.createType,
  (value) => {
    if (value === 'tool') {
      showUpdateModal.value = false
      resetForm()
    }
  },
  { immediate: true },
)
watch(
  () => form.openapi_schema,
  () => {
    schemaError.value = ''
  },
)
onMounted(initData)
watch(() => route.query.search_word, initData)
</script>

<template>
  <a-spin
    :loading="loading"
    class="block h-full w-full scrollbar-w-none overflow-scroll"
    @scroll="handleScroll"
  >
    <!-- 底部插件列表 -->
    <a-row :gutter="[20, 20]" class="flex-1">
      <!-- 有数据的UI状态 -->
      <a-col v-for="provider in providers" :key="provider.id" :xs="24" :sm="12" :lg="8" :xl="6">
        <a-card
          hoverable
          class="cursor-pointer rounded-lg"
          @click="selectedProviderId = provider.id"
        >
          <!-- 顶部提供商名称 -->
          <div class="flex items-center gap-3 mb-3">
            <!-- 左侧图标 -->
            <a-avatar :size="40" shape="square" :image-url="provider.icon" />
            <!-- 右侧工具信息 -->
            <div class="flex flex-col">
              <div class="text-base text-gray-900 font-bold">{{ provider.name }}</div>
              <div class="text-xs text-gray-500 line-clamp-1">
                提供商 {{ provider.name }} · {{ provider.tools.length }} 插件
              </div>
            </div>
          </div>
          <!-- 提供商的描述信息 -->
          <div class="leading-[18px] text-gray-500 h-[72px] line-clamp-4 mb-2">
            {{ provider.description }}
          </div>
          <!-- 提供商的发布信息 -->
          <div class="flex items-center gap-1.5">
            <a-avatar :size="18" class="bg-blue-700">
              <icon-user />
            </a-avatar>
            <div class="text-xs text-gray-400">
              创建时间
              {{ moment(provider.created_at * 1000).format('MM-DD HH:mm') }}
            </div>
          </div>
        </a-card>
      </a-col>
      <!-- 没数据的UI状态 -->
      <a-col v-if="!loading && !loadError && providers.length === 0" :span="24">
        <a-empty
          description="没有可用的API插件"
          class="h-[400px] flex flex-col items-center justify-center"
        />
      </a-col>
    </a-row>
    <a-alert v-if="loadError" type="error" class="my-4">
      {{ loadError }}
      <template #action
        ><a-button size="small" @click="providers.length ? loadMoreData() : initData()"
          >重试</a-button
        ></template
      >
    </a-alert>
    <!-- 加载器 -->
    <a-row v-if="paginator.total_page >= 2">
      <!-- 加载数据中 -->
      <a-col v-if="paginator.current_page <= paginator.total_page" :span="24" align="center">
        <a-space class="my-4">
          <a-spin />
          <div class="text-gray-400">加载中</div>
        </a-space>
      </a-col>
      <!-- 数据加载完成 -->
      <a-col v-else :span="24" align="center">
        <div class="text-gray-400 my-4">数据已加载完成</div>
      </a-col>
    </a-row>
    <!-- 卡片抽屉 -->
    <a-drawer
      :visible="!!selectedProvider"
      :width="650"
      :footer="false"
      title="工具详情"
      :drawer-style="{ background: '#F9FAFB' }"
      @cancel="selectedProviderId = ''"
    >
      <!-- 详情按提供者 ID 选择，避免列表刷新后索引错位 -->
      <div v-if="selectedProvider" class="">
        <!-- 顶部提供商名称 -->
        <div class="flex items-center gap-3 mb-3">
          <!-- 左侧图标 -->
          <a-avatar :size="40" shape="square" :image-url="selectedProvider.icon" />
          <!-- 右侧工具信息 -->
          <div class="flex flex-col">
            <div class="text-base text-gray-900 font-bold">
              {{ selectedProvider.name }}
            </div>
            <div class="text-xs text-gray-500 line-clamp-1">
              提供商 {{ selectedProvider.name }} · {{ selectedProvider.tools.length }} 插件
            </div>
          </div>
        </div>
        <!-- 提供商的描述信息 -->
        <div class="leading-[18px] text-gray-500 mb-4">
          {{ selectedProvider.description }}
        </div>
        <!-- 编辑按钮 -->
        <a-button
          :loading="showUpdateModalLoading"
          type="dashed"
          long
          class="mb-2 rounded-lg"
          @click="handleUpdate"
        >
          <template #icon>
            <icon-settings />
          </template>
          编辑工具
        </a-button>
        <!-- 分隔符 -->
        <hr class="my-4" />
        <a-alert v-if="selectedProvider.schemaError" type="warning" class="mb-4">{{
          selectedProvider.schemaError
        }}</a-alert>
        <!-- 提供者工具 -->
        <div class="flex flex-col gap-2">
          <div class="text-xs text-gray-500">包含 {{ selectedProvider.tools.length }} 个工具</div>
          <!-- 工具列表 -->
          <a-card
            v-for="tool in selectedProvider.tools"
            :key="tool.name"
            class="cursor-pointer flex flex-col rounded-xl"
          >
            <!-- 工具名称 -->
            <div class="font-bold text-gray-900 mb-2">{{ tool.name }}</div>
            <!-- 工具描述 -->
            <div class="text-gray-500 text-xs">{{ tool.description }}</div>
            <!-- 工具参数 -->
            <div v-if="tool.inputs.length > 0" class="">
              <!-- 分隔符 -->
              <div class="flex items-center gap-2 my-4">
                <div class="text-xs font-bold text-gray-500">参数</div>
                <hr class="flex-1" />
              </div>
              <!-- 参数列表 -->
              <div class="flex flex-col gap-4">
                <div v-for="input in tool.inputs" :key="input.name" class="flex flex-col gap-2">
                  <!-- 上半部分 -->
                  <div class="flex items-center gap-2 text-xs">
                    <div class="text-gray-900 font-bold">{{ input.name }}</div>
                    <div class="text-gray-500">{{ typeMap[input.type] }}</div>
                    <div v-if="input.required" class="text-red-700">必填</div>
                  </div>
                  <!-- 参数描述信息 -->
                  <div class="text-xs text-gray-500">{{ input.description }}</div>
                </div>
              </div>
            </div>
          </a-card>
        </div>
      </div>
    </a-drawer>
    <!-- 新建/修改模态窗 -->
    <a-modal
      :width="830"
      :visible="props.createType === 'tool' || showUpdateModal"
      hide-title
      :mask-closable="!submitLoading"
      :esc-to-close="!submitLoading"
      :footer="false"
      modal-class="rounded-xl"
      @cancel="handleCancel"
    >
      <!-- 顶部标题 -->
      <div class="flex items-center justify-between">
        <div class="text-lg font-bold text-gray-700">
          {{ props.createType === 'tool' ? '新建' : '更新' }}插件
        </div>
        <a-button type="text" class="!text-gray-700" size="small" @click="handleCancel">
          <template #icon>
            <icon-close />
          </template>
        </a-button>
      </div>
      <!-- 中间表单 -->
      <div class="pt-6">
        <a-form
          ref="formRef"
          :model="form"
          :disabled="submitLoading"
          @submit="handleSubmit"
          layout="vertical"
        >
          <a-form-item
            field="icon"
            hide-label
            :rules="[{ required: true, message: '插件图标不能为空' }]"
          >
            <a-upload
              :limit="1"
              list-type="picture-card"
              accept="image/png, image/jpeg"
              class="!w-auto mx-auto"
              v-model:file-list="form.fileList"
              image-preview
              :custom-request="
                uploadAdapter(async (option) => {
                  const { fileItem, onSuccess, onError } = option
                  const resp = await uploadImage(fileItem.file as File)
                  form.icon = resp.data.image_url
                  onSuccess(resp)
                })
              "
              :on-before-remove="
                async () => {
                  form.icon = ''
                  return true
                }
              "
            />
          </a-form-item>
          <a-form-item
            field="name"
            label="插件名称"
            asterisk-position="end"
            :rules="[
              { required: true, message: '插件名称不能为空' },
              { maxLength: 30, message: '插件名称最多30字' },
              {
                validator: (value, callback) =>
                  callback(value.trim() ? undefined : '插件名称不能为空'),
              },
            ]"
          >
            <a-input
              v-model="form.name"
              placeholder="请输入插件名称，确保名称含义清晰"
              show-word-limit
              :max-length="30"
            />
          </a-form-item>
          <a-form-item
            field="openapi_schema"
            label="OpenAPI Schema"
            :validate-status="schemaError ? 'error' : undefined"
            :help="
              schemaError ||
              '使用本项目简化的 JSON Schema。鉴权信息请填写到下方 Headers，可查看填写规则与示例。'
            "
            asterisk-position="end"
            :rules="[
              { required: true, message: 'OpenAPI Schema不能为空' },
              {
                validator: (value, callback) =>
                  callback(value.trim() ? undefined : 'OpenAPI Schema不能为空'),
              },
            ]"
          >
            <div class="w-full min-w-0">
              <a-button
                type="text"
                size="small"
                html-type="button"
                class="mb-2"
                :aria-expanded="showSchemaGuide"
                aria-controls="api-tool-schema-guide"
                @click="showSchemaGuide = !showSchemaGuide"
              >
                {{ showSchemaGuide ? '收起规则与示例' : '填写规则与示例' }}
              </a-button>
              <div
                v-if="showSchemaGuide"
                id="api-tool-schema-guide"
                class="mb-3 rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600 leading-6"
              >
                <p class="font-medium text-gray-800">
                  使用 JSON 格式，本项目仅支持简化规范，不支持完整 OpenAPI 3.0。
                </p>
                <ul class="list-disc pl-4 my-2">
                  <li>
                    <code>server</code>：HTTP/HTTPS
                    服务基础地址；<code>description</code>：工具提供者描述，均必填。
                  </li>
                  <li>
                    <code>paths</code>：至少包含一个接口，路径以 / 开头，方法使用小写
                    <code>get</code> 或 <code>post</code>。
                  </li>
                  <li>
                    每个接口填写 <code>operationId</code>（整个 Schema 内唯一的工具名称）和
                    <code>description</code>（工具用途）。
                  </li>
                  <li>
                    <code>parameters</code> 为参数数组，无参数时可省略或填写
                    []；同一接口内参数名称不能重复。
                  </li>
                  <li>
                    每个参数必须包含
                    <code>name</code
                    >、<code>in</code>、<code>description</code>、<code>required</code>、<code>type</code>。
                  </li>
                  <li>
                    <code>type</code> 支持 str / int / float / bool；<code>required</code> 填 true
                    或 false，不加引号。
                  </li>
                  <li>
                    <code>in</code> 支持 query（网址查询参数）、request_body（JSON
                    请求体）、path（路径）、header（请求头）、cookie。
                  </li>
                  <li>
                    路径参数如 /products/{id}，必须定义同名的 path 参数，并设置 required 为 true。
                  </li>
                  <li>
                    不支持 default
                    默认值、嵌套对象或数组类型。描述中的建议值不会自动填入，省略可选参数时不会发送该参数。
                  </li>
                </ul>
                <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <a-radio-group
                    v-model="schemaExampleType"
                    type="button"
                    size="small"
                    aria-label="示例类型"
                  >
                    <a-radio value="get">GET 查询参数</a-radio>
                    <a-radio value="post">POST 博查搜索</a-radio>
                  </a-radio-group>
                  <a-button size="small" html-type="button" @click="copySchemaExample"
                    >复制示例</a-button
                  >
                </div>
                <p v-if="schemaExampleType === 'get'" class="mb-2">
                  此为格式示例，请替换服务地址、路径和参数后使用。
                </p>
                <div v-else class="mb-2 break-words">
                  <p>在下方 Headers 单独添加两行（密钥替换为你的博查 API Key）：</p>
                  <p><code>Authorization: Bearer 你的博查APIKey</code>（Bearer 后有一个空格）</p>
                  <p><code>Content-Type: application/json</code></p>
                </div>
                <pre
                  class="max-h-64 overflow-auto rounded border border-gray-200 bg-white p-3 text-xs leading-5 select-text"
                  tabindex="0"
                  aria-label="可复制的 Schema 示例"
                ><code>{{ schemaExample }}</code></pre>
                <p class="mt-2">
                  复制示例不会替换当前输入。保存时仅校验配置格式，实际调用需在应用中调试。
                </p>
              </div>
              <a-textarea
                v-model="form.openapi_schema"
                :auto-size="{ minRows: 4, maxRows: 6 }"
                placeholder="在此处输入您的 OpenAPI Schema"
                @blur="validateSchema()"
              />
            </div>
          </a-form-item>
          <a-form-item label="可用工具">
            <!-- 可用工具表格 -->
            <div class="rounded-lg border border-gray-200 w-full overflow-x-auto">
              <table class="w-full leading-[18px] text-xs text-gray-700 font-normal">
                <thead class="text-gray-500">
                  <tr class="border-b border-gray-200">
                    <th class="p-2 pl-3 font-medium">名称</th>
                    <th class="p-2 pl-3 font-medium w-[236px]">描述</th>
                    <th class="p-2 pl-3 font-medium">方法</th>
                    <th class="p-2 pl-3 font-medium">路径</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(tool, idx) in tools"
                    :key="idx"
                    class="border-b last:border-0 border-gray-200 text-gray-700"
                  >
                    <td class="p-2 pl-3">{{ tool.name }}</td>
                    <td class="p-2 pl-3 w-[236px]">{{ tool.description }}</td>
                    <td class="p-2 pl-3">{{ tool.method }}</td>
                    <td class="p-2 pl-3 w-[62px]">{{ tool.path }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </a-form-item>
          <a-form-item label="Headers">
            <!-- 请求头表单 -->
            <div class="rounded-lg border border-gray-200 w-full overflow-x-auto">
              <table class="w-full leading-[18px] text-xs text-gray-700 font-normal mb-3">
                <thead class="text-gray-500">
                  <tr class="border-b border-gray-200">
                    <th class="p-2 pl-3 font-medium">Key</th>
                    <th class="p-2 pl-3 font-medium">Value</th>
                    <th class="p-2 pl-3 font-medium w-[50px]">操作</th>
                  </tr>
                </thead>
                <tbody v-if="form.headers.length > 0" class="border-b border-gray-200">
                  <tr
                    v-for="(header, idx) in form.headers"
                    :key="idx"
                    class="border-b last:border-0 border-gray-200"
                  >
                    <td class="p-2 pl-3">
                      <a-form-item
                        :field="`headers[${idx}].key`"
                        hide-label
                        class="m-0"
                        :rules="[
                          { required: true, message: '请求头名称不能为空' },
                          {
                            validator: (value, callback) =>
                              callback(
                                value.trim() && !/[\r\n]/.test(value)
                                  ? undefined
                                  : '请求头名称不合法',
                              ),
                          },
                        ]"
                      >
                        <a-input v-model="header.key" placeholder="请输入请求头键名" />
                      </a-form-item>
                    </td>
                    <td class="p-2 pl-3">
                      <a-form-item
                        :field="`headers[${idx}].value`"
                        hide-label
                        class="m-0"
                        :rules="[
                          {
                            validator: (value, callback) =>
                              callback(!/[\r\n]/.test(value) ? undefined : '请求头值不能包含换行'),
                          },
                        ]"
                      >
                        <a-input v-model="header.value" placeholder="请输入请求头键值内容" />
                      </a-form-item>
                    </td>
                    <td class="p-2 pl-3">
                      <a-button
                        size="mini"
                        type="text"
                        class="!text-gray-700"
                        @click="form.headers.splice(idx, 1)"
                      >
                        <template #icon>
                          <icon-delete />
                        </template>
                      </a-button>
                    </td>
                  </tr>
                </tbody>
              </table>
              <a-button
                size="mini"
                class="rounded ml-3 mb-3 !text-gray-700"
                @click="form.headers.push({ key: '', value: '' })"
              >
                <template #icon>
                  <icon-plus />
                </template>
                增加参数
              </a-button>
            </div>
          </a-form-item>
          <!-- 底部按钮 -->
          <div class="flex items-center justify-between">
            <div class="">
              <a-button
                v-if="showUpdateModal"
                class="rounded-lg !text-red-700"
                @click="handleDelete"
              >
                删除
              </a-button>
            </div>
            <a-space :size="16">
              <a-button class="rounded-lg" @click="handleCancel">取消</a-button>
              <a-button
                :loading="submitLoading"
                type="primary"
                html-type="submit"
                class="rounded-lg"
              >
                保存
              </a-button>
            </a-space>
          </div>
        </a-form>
      </div>
    </a-modal>
  </a-spin>
</template>

<style scoped></style>
