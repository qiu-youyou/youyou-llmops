const processingLabels: Record<string, string> = {
  waiting: '等待处理',
  parsing: '正在解析',
  splitting: '正在分段',
  indexing: '正在构建索引',
}

export const isDocumentProcessing = (status: string) =>
  Object.prototype.hasOwnProperty.call(processingLabels, status)

export const documentStatusLabel = (status: string, enabled?: boolean) => {
  if (isDocumentProcessing(status)) return processingLabels[status]
  if (status === 'error') return '处理失败'
  if (status === 'completed') {
    return enabled === undefined ? '处理完成' : enabled ? '可用' : '已禁用'
  }
  return '未知状态'
}
