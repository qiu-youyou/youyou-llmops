<script setup lang="ts">
import { useRoute } from 'vue-router'
import { ref } from 'vue'

const route = useRoute()
const create_api_key = ref(false)
</script>

<template>
  <div class="px-6 flex flex-col overflow-hidden h-full min-h-0">
    <div class="pt-6 shrink-0 z-20 bg-gray-50">
      <!-- 顶层标题+创建按钮 -->
      <div class="flex items-center justify-between mb-6">
        <!-- 左侧标题 -->
        <div class="flex items-center gap-2">
          <a-avatar :size="32" class="bg-blue-700">
            <icon-user :size="18" />
          </a-avatar>
          <div class="flex items-center gap-2 text-lg font-medium text-gray-900">
            开放API
            <div class="text-xs text-gray-500">利用开放 API 快速与企业现有业务对接</div>
          </div>
        </div>
      </div>
      <!-- 导航按钮 -->
      <div class="flex items-center justify-between mb-6">
        <!-- 左侧导航 -->
        <div class="flex items-center gap-2">
          <router-link
            to="/openapi"
            class="rounded-lg text-gray-700 px-3 h-8 leading-8 hover:bg-gray-200 transition-all"
            exact-active-class="bg-gray-100"
          >
            快速开始
          </router-link>
          <router-link
            to="/openapi/api-keys"
            class="rounded-lg text-gray-700 px-3 h-8 leading-8 hover:bg-gray-200 transition-all"
            active-class="bg-gray-100"
          >
            秘钥
          </router-link>
        </div>
        <!-- 右侧按钮 -->
        <a-button
          v-if="route.path.startsWith('/openapi/api-keys')"
          type="primary"
          class="rounded-lg"
          @click="create_api_key = true"
        >
          新增秘钥
        </a-button>
      </div>
    </div>
    <!-- 中间内容 -->
    <div class="openapi-content flex-1 min-h-0 overflow-auto pb-6">
      <router-view v-model:create_api_key="create_api_key" />
    </div>
  </div>
</template>

<style scoped></style>
