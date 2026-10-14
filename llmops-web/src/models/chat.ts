/** Shared payload for debugger, WebApp and assistant chat. */
export interface ChatRequest {
  query: string
  image_urls?: string[]
  conversation_id?: string
}

export const MAX_MESSAGE_IMAGES = 5
export const MAX_IMAGE_FILE_SIZE = 15 * 1024 * 1024
export const CHAT_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp']
