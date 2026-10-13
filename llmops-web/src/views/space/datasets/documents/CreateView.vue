<script setup lang="ts">
import { uploadAdapter } from '@/utils/upload-adapter'
import { onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { type Form, Message } from '@arco-design/web-vue'
import { createDocuments, getDocumentsStatus } from '@/services/dataset'
import { uploadFile } from '@/services/upload-file'
import { unescapeString } from '@/utils/helper'
import DocumentStatus from './components/DocumentStatus.vue'

// 1.定义页面逻辑基础数据，涵盖定时器、路由、当前步骤书、表单信息等
let timer: ReturnType<typeof setTimeout> | null = null
let batch = ''
let fetchCount = 0
let disposed = false
const pollingError = ref('')
const pollingBusy = ref(false)
const route = useRoute()
const currentStep = ref(1)
const createDocumentsForm = reactive({
  file_list: [] as any[],
  process_type: 'automatic',
  rule: {
    separators: ['\\n'],
    chunk_size: 500,
    chunk_overlap: 50,
    pre_process_rules: [] as string[],
  },
})
const customRuleFormRef = ref<InstanceType<typeof Form>>()
const createDocumentsLoading = ref(false)
const documents = reactive<Array<any>>([])

// 2.定义下一步处理函数
const nextStep = async () => {
  // 2.1 判断下当前所处的步骤并执行不同的操作
  if (currentStep.value === 1) {
    // 2.2 检查是否已经上传了文件，如果没上传则不允许点击下一步
    if (createDocumentsForm.file_list.length === 0) {
      Message.error('请上传需要添加到知识库的文件')
      return
    }

    // 2.3 检查所有文件是否全部上传完成
    const isUploaded = createDocumentsForm.file_list.every(
      (fileItem) => fileItem.response?.data?.id,
    )
    if (!isUploaded) {
      Message.warning('文件正在上传中，请稍等')
      return
    }

    // 2.4 进入下一步
    currentStep.value++
  } else {
    // 2.5 当前处于第2页，需要根据不同的处理类型执行不同的操作
    if (createDocumentsForm.process_type === 'custom') {
      // 2.6 校验表单数据监测是否出错
      const errors = await customRuleFormRef.value?.validate()
      if (errors) return
    }

    // 2.7 如果校验成功或者是自动规则，则执行下一步
    try {
      // 2.8 将加载状态设置为true，并将表单数据转换成api接口数据
      createDocumentsLoading.value = true
      const req: any = {
        upload_file_ids: createDocumentsForm.file_list.map(
          (fileItem) => fileItem?.response?.data?.id,
        ),
        process_type: createDocumentsForm.process_type,
      }

      // 2.9 如果处理类型为自定义，则需要添加上自定义规则
      if (createDocumentsForm.process_type === 'custom') {
        req.rule = {
          pre_process_rules: [
            {
              id: 'remove_extra_space',
              enabled: createDocumentsForm.rule.pre_process_rules.includes('remove_extra_space'),
            },
            {
              id: 'remove_url_and_email',
              enabled: createDocumentsForm.rule.pre_process_rules.includes('remove_url_and_email'),
            },
          ],
          segment: {
            separators: createDocumentsForm.rule.separators.map((separator) =>
              unescapeString(separator),
            ),
            chunk_size: createDocumentsForm.rule.chunk_size,
            chunk_overlap: createDocumentsForm.rule.chunk_overlap,
          },
        }
      }

      // 2.10 发起请求并获取数据
      const resp = await createDocuments(route.params?.dataset_id as string, req)
      batch = resp.data.batch

      // 2.11 先调用一次获取文档状态，然后创建定时器
      currentStep.value = 3
      documents.splice(0, documents.length, ...resp.data.documents)
      await fetchDocumentsStatus()
    } finally {
      createDocumentsLoading.value = false
    }
  }
}

// 3.定义获取文档状态数据函数
const fetchDocumentsStatus = async () => {
  if (disposed || pollingBusy.value) return
  pollingBusy.value = true
  pollingError.value = ''
  try {
    fetchCount++
    const { data } = await getDocumentsStatus(route.params.dataset_id as string, batch)
    if (disposed) return
    documents.splice(0, documents.length, ...data)
    const complete = data.length > 0 && data.every((d) => ['completed', 'error'].includes(d.status))
    if (!complete && fetchCount < 30) timer = setTimeout(fetchDocumentsStatus, 5000)
    else if (!complete)
      pollingError.value = '处理仍在继续，已暂停自动刷新。可手动刷新或稍后返回文档列表。'
  } catch (e: any) {
    pollingError.value = e.message || '无法获取处理状态，请重试'
  } finally {
    pollingBusy.value = false
  }
}
const resumePolling = () => {
  fetchCount = 0
  stopTimer()
  void fetchDocumentsStatus()
}

// 5.停止定时器函数
const stopTimer = () => {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

// 6.页面卸载时同步卸载定时器
onUnmounted(() => {
  disposed = true
  stopTimer()
})
</script>

<template>
  <div class="p-6">
    <!-- 回退按钮与标题 -->
    <div class="flex items-center mb-6 gap-4">
      <!-- 左侧回退按钮 -->
      <router-link
        :to="{
          name: 'space-datasets-documents-list',
          params: { dataset_id: route.params?.dataset_id as string },
        }"
      >
        <a-button size="mini" type="text" class="!text-gray-700">
          <template #icon>
            <icon-left />
          </template>
        </a-button>
      </router-link>
      <div class="text-lg font-bold text-gray-700">添加文件</div>
    </div>
    <!-- 步骤条 -->
    <div class="w-[520px] mx-auto">
      <a-steps :current="currentStep">
        <a-step>上传</a-step>
        <a-step>分段设置</a-step>
        <a-step>数据处理</a-step>
      </a-steps>
    </div>
    <!-- 步骤条页面 -->
    <div class="min-h-[calc(100vh-160px)] p-[48px]">
      <!-- 上传页面 -->
      <div v-if="currentStep === 1" class="">
        <!-- 上传文件按钮 -->
        <a-upload
          v-model:file-list="createDocumentsForm.file_list"
          draggable
          accept=".doc,.docx,.pdf,.txt,.md,.markdown"
          :limit="10"
          multiple
          tip="支持PDF、TXT、DOC、DOCX、MD，最多可上传10个文件，每个文件的大小不超过10MB"
          :custom-request="
            uploadAdapter(async (option) => {
              // 1.提取选项中的文件选项以及成功回调
              const { fileItem, onSuccess } = option

              // 2.调用api接口上传文件并添加数据
              const resp = await uploadFile(fileItem.file!)
              onSuccess(resp)
            })
          "
        />
      </div>
      <!-- 分段设置页面 -->
      <div v-else-if="currentStep === 2" class="">
        <!-- 自动分段与清洗 -->
        <div
          :class="`px-5 py-4 bg-white rounded-lg border cursor-pointer mb-4 hover:border-blue-700 ${createDocumentsForm.process_type === 'automatic' ? 'border-blue-700' : ''}`"
          @click="createDocumentsForm.process_type = 'automatic'"
        >
          <div class="font-bold text-gray-700 mb-2">自动分段与清洗</div>
          <div class="text-gray-500">自动分段与预处理规则</div>
        </div>
        <!-- 自定义 -->
        <div
          :class="`px-5 py-4 bg-white rounded-lg border cursor-pointer hover:border-blue-700 ${createDocumentsForm.process_type === 'custom' ? 'border-blue-700' : ''}`"
          @click="createDocumentsForm.process_type = 'custom'"
        >
          <div class="font-bold text-gray-700 mb-2">自定义</div>
          <div class="text-gray-500">自定义分段规则、分段长度与预处理规则</div>
          <!-- 自定义表单 -->
          <div v-if="createDocumentsForm.process_type === 'custom'" class="">
            <a-divider />
            <!-- 表单选项 -->
            <a-form :model="createDocumentsForm.rule" ref="customRuleFormRef" layout="vertical">
              <a-form-item
                field="separators"
                label="分段标识符"
                required
                asterisk-position="end"
                :rules="[{ required: true, message: '分段标识符不能为空' }]"
              >
                <a-input-tag
                  v-model:model-value="createDocumentsForm.rule.separators"
                  placeholder="请输入分段标识符，按下Enter结束"
                />
              </a-form-item>
              <a-form-item
                field="chunk_size"
                label="分段最大长度"
                required
                asterisk-position="end"
                :rules="[{ required: true, message: '分段最大长度不能为空' }]"
              >
                <a-input-number
                  v-model:model-value="createDocumentsForm.rule.chunk_size"
                  :min="100"
                  :max="1000"
                  :step="1"
                  :default-value="500"
                  placeholder="请输入100-1000的数字"
                />
              </a-form-item>
              <a-form-item
                field="chunk_overlap"
                label="块重叠数"
                required
                asterisk-position="end"
                :rules="[{ required: true, message: '块重叠大小不能为空' }]"
              >
                <a-input-number
                  v-model:model-value="createDocumentsForm.rule.chunk_overlap"
                  :min="0"
                  :max="500"
                  :step="1"
                  :default-value="50"
                  placeholder="请输入0-500的数字"
                />
              </a-form-item>
              <a-form-item field="pre_process_rules" label="文本预处理规则">
                <a-checkbox-group
                  v-model:model-value="createDocumentsForm.rule.pre_process_rules"
                  direction="vertical"
                >
                  <a-checkbox value="remove_extra_space">
                    替换掉连续的空格、换行符和制表符
                  </a-checkbox>
                  <a-checkbox value="remove_url_and_email">删除所有 URL 和电子邮件</a-checkbox>
                </a-checkbox-group>
              </a-form-item>
            </a-form>
          </div>
        </div>
      </div>
      <!-- 数据处理页面 -->
      <div v-else class="">
        <!-- 数据处理状态提示 -->
        <div class="text-gray-900 mb-4 text-base">服务器正在处理中</div>
        <a-alert v-if="pollingError" type="warning" class="mb-4"
          >{{ pollingError }}
          <a-button :loading="pollingBusy" size="small" @click="resumePolling"
            >刷新状态</a-button
          ></a-alert
        >
        <!-- 处理中的文档列表 -->
        <div class="flex flex-col gap-2">
          <div
            v-for="document in documents"
            :key="document.id"
            class="flex items-center justify-between px-4 py-3 bg-white rounded-lg border"
          >
            <!-- 左侧文件信息 -->
            <div class="flex items-center gap-2.5">
              <a-avatar shape="square" class="bg-blue-700" :size="32">
                <icon-file />
              </a-avatar>
              <div class="">
                <div class="text-gray-700">{{ document.name }}</div>
                <div class="text-gray-500">{{ (document.size / 1024).toFixed(2) }}kb</div>
              </div>
            </div>
            <!-- 处理阶段与索引片段进度 -->
            <div class="flex items-center gap-2">
              <DocumentStatus :status="document.status" :error="document.error" />
              <span
                v-if="document.status === 'indexing' && document.segment_count > 0"
                class="text-gray-500"
              >
                {{
                  ((document.completed_segment_count / document.segment_count) * 100).toFixed(2)
                }}%
              </span>
              <span v-if="document.status === 'error'" class="text-red-700">
                {{ document.error || '请检查文件内容' }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
    <!-- 按钮：涵盖上一步和下一步 -->
    <div class="flex items-center justify-between px-[48px]">
      <div class=""></div>
      <div class="flex items-center gap-2">
        <a-button
          v-if="currentStep === 2"
          class="rounded-lg"
          @click="
            () => {
              if (currentStep > 1) currentStep--
            }
          "
        >
          上一步
        </a-button>
        <a-button
          :loading="createDocumentsLoading"
          v-if="currentStep <= 2"
          type="primary"
          class="rounded-lg"
          @click="nextStep"
        >
          下一步
        </a-button>
        <!-- 数据处理页面显示的内容 -->
        <div v-if="currentStep === 3" class="flex items-center gap-2">
          <div class="text-gray-500">点击确认不影响数据处理，处理完毕后可进行引用</div>
          <router-link
            :to="{
              name: 'space-datasets-documents-list',
              params: { dataset_id: route.params?.dataset_id as string },
            }"
          >
            <a-button type="primary" class="rounded-lg">确定</a-button>
          </router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped></style>
