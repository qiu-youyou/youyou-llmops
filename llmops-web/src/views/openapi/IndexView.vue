<script setup lang="ts">
import { computed, ref } from 'vue'
import { apiPrefix } from '@/config'
import { Message } from '@arco-design/web-vue'
const stream = ref(true)
const example = computed(
  () =>
    `curl '${apiPrefix}/openapi/chat' \\\n  -H 'Authorization: Bearer YOUR_API_KEY' \\\n  -H 'Content-Type: application/json' \\\n  -d '${JSON.stringify({ app_id: 'YOUR_APP_ID', query: '你好', stream: stream.value }, null, 2)}'`,
)
async function copy() {
  try {
    await navigator.clipboard.writeText(example.value)
    Message.success('已复制示例')
  } catch {
    Message.error('无法复制，请手动选择代码')
  }
}
</script>
<template>
  <section class="panel">
    <p class="eyebrow">DEVELOPER / QUICK START</p>
    <h1>将 Youyou 接入你的产品</h1>
    <p class="page-description">
      先发布一个应用，再创建 API 密钥，即可从你的服务发起对话。请将密钥保存在服务端。
    </p>
    <div class="flex items-center justify-between mt-8 mb-4">
      <h2>发送第一条消息</h2>
      <a-switch v-model="stream" checked-text="流式" unchecked-text="非流式" />
    </div>
    <pre class="code-panel">{{ example }}</pre>
    <a-button @click="copy">复制调用示例</a-button>
    <div class="mt-8">
      <h2>图片输入</h2>
      <p class="page-description">
        选择支持图片的模型后，添加 image_urls 数组（最多5张 HTTPS 图片）。纯图片消息可将 query
        设为空字符串。图片地址需要保持可访问，以支持历史追问。
      </p>
      <pre class="code-panel">{{
        JSON.stringify(
          {
            app_id: 'YOUR_APP_ID',
            query: '对比这两张图片',
            image_urls: ['https://example.com/a.png', 'https://example.com/b.jpg'],
            stream: true,
          },
          null,
          2,
        )
      }}</pre>
      <pre class="code-panel">{{
        JSON.stringify(
          {
            app_id: 'YOUR_APP_ID',
            query: '',
            image_urls: ['https://example.com/a.png'],
            stream: true,
          },
          null,
          2,
        )
      }}</pre>
    </div>
    <div class="mt-8">
      <h2>继续对话与处理结果</h2>
      <p class="page-description">
        多轮对话需要同时传递上次响应的 conversation_id 和 end_user_id。流式响应使用 SSE 格式；收到
        agent_end、stop、timeout 或 error 后结束读取。error 事件中的 observation 是错误说明，即使
        HTTP 状态为 200 也不能视为成功。
      </p>
      <p class="page-description">
        金额可能是字符串或 null。usage.complete 为 false
        表示统计不完整；页面中的费用按本地价目表计算，不代表供应商实际扣款。
      </p>
    </div>
  </section>
</template>
<style scoped>
.code-panel {
  background: #182b25;
  color: #d5e8df;
  border-radius: 10px;
  padding: 24px;
  margin-bottom: 16px;
  font-size: 12px;
  line-height: 1.8;
}
</style>
