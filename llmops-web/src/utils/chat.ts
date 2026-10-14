import { terminalEvents, type StreamFrame } from './sse'
export interface ChatMessage {
  id: string
  query: string
  image_urls?: string[]
  answer: string
  conversation_id?: string
  task_id?: string
  error?: string
  status?: string
  usage?: Record<string, any>
  agent_thoughts: Record<string, any>[]
  created_at?: number
}
export function applyChatFrame(message: ChatMessage, frame: StreamFrame) {
  const { event, data } = frame
  if (event === 'ping') return
  if (data.message_id) message.id = data.message_id
  if (data.task_id) message.task_id = data.task_id
  if (data.conversation_id) message.conversation_id = data.conversation_id
  if (terminalEvents.has(event)) {
    message.status = event
    if (data.usage) message.usage = data.usage
    if (event === 'error' || event === 'timeout')
      message.error = data.observation || (event === 'timeout' ? '运行超时' : '运行失败')
    return
  }
  if (event === 'agent_message') message.answer += data.answer || ''
  const index = message.agent_thoughts.findIndex((item) => item.id === data.id)
  if (index < 0) message.agent_thoughts.push({ ...data, event })
  else {
    const old = message.agent_thoughts[index]
    message.agent_thoughts[index] = {
      ...old,
      ...data,
      usage: data.usage || old.usage,
      thought: (old.thought || '') + (event === 'agent_message' ? data.thought || '' : ''),
      answer: (old.answer || '') + (data.answer || ''),
    }
  }
}
export function usageLabel(usage?: Record<string, any>) {
  if (!usage || Object.keys(usage).length === 0) return '用量暂无数据'
  const costs =
    usage.known_costs &&
    Object.entries(usage.known_costs)
      .map(([currency, cost]) => `${cost} ${currency}`)
      .join(' · ')
  return `${usage.complete ? '' : '统计不完整 · '}${usage.total_price == null ? '费用未知' : costs || `${usage.total_price} ${usage.currency || ''}`} · 按价目表计算`
}
