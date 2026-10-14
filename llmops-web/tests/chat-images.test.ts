// @vitest-environment jsdom
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ChatPanel from '../src/components/ChatPanel.vue'
import ChatImages from '../src/components/ChatImages.vue'
import { uploadImage } from '../src/services/upload-file'
import { get, ssePost } from '../src/utils/request'
vi.mock('../src/services/upload-file', () => ({ uploadImage: vi.fn() }))
vi.mock('../src/utils/request', () => ({ get: vi.fn(), post: vi.fn(), ssePost: vi.fn() }))
vi.mock('../src/services/ai', () => ({ generateSuggestedQuestions: vi.fn() }))
vi.mock('../src/utils/draft-queue', () => ({ appDraftQueueFor: () => ({ flush: vi.fn() }) }))
const url = 'https://example.com/image.png'
const wrappers: ReturnType<typeof mount>[] = []
const global = {
  stubs: {
    MarkdownContent: true,
    'a-button': {
      props: ['disabled', 'htmlType'],
      template: '<button :disabled="disabled" :type="htmlType || \'button\'"><slot /></button>',
    },
    'a-image': { props: ['src'], template: '<img :src="src" />' },
    'a-spin': true,
    'icon-arrow-up': true,
    'icon-arrow-right': true,
  },
}
function panel(imageInput = true) {
  const wrapper = mount(ChatPanel, {
    props: {
      endpoint: '/chat',
      historyEndpoint: '/history',
      stopEndpoint: () => '/stop',
      modelFeatures: imageInput ? ['image_input'] : [],
    },
    global,
  })
  wrappers.push(wrapper)
  return wrapper
}
async function choose(wrapper: ReturnType<typeof panel>) {
  const input = wrapper.get('input[type=file]')
  Object.defineProperty(input.element, 'files', {
    configurable: true,
    value: [new File(['image'], 'test.png', { type: 'image/png' })],
  })
  await input.trigger('change')
  await flushPromises()
}
beforeEach(() => {
  vi.resetAllMocks()
  URL.createObjectURL = vi.fn(() => 'blob:preview')
  URL.revokeObjectURL = vi.fn()
  Element.prototype.scrollTo = vi.fn()
  vi.mocked(get).mockResolvedValue({ data: { list: [], paginator: {} } })
  vi.mocked(uploadImage).mockResolvedValue({ data: { image_url: url } } as any)
  vi.mocked(ssePost).mockImplementation(async (_url, _options, callback) => {
    callback({ event: 'agent_end', data: { message_id: 'm1', conversation_id: 'c1' } })
  })
})
afterEach(() => wrappers.splice(0).forEach((w) => w.unmount()))
describe('chat images', () => {
  it('uploads and sends an image-only message, then displays it', async () => {
    const wrapper = panel()
    await choose(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(ssePost).toHaveBeenCalledWith(
      '/chat',
      expect.objectContaining({ body: { query: '', image_urls: [url] } }),
      expect.any(Function),
    )
    expect(wrapper.find('.human-bubble img').attributes('src')).toBe(url)
    expect(wrapper.findAll('.pending-image')).toHaveLength(0)
  })
  it('blocks failed uploads, retries, and allows removing attachments', async () => {
    vi.mocked(uploadImage).mockRejectedValueOnce(new Error('upload failed'))
    const wrapper = panel()
    await choose(wrapper)
    expect(wrapper.text()).toContain('upload failed')
    await wrapper.get('form').trigger('submit')
    expect(ssePost).not.toHaveBeenCalled()
    const retry = wrapper.findAll('button').find((b) => b.text() === '重试上传')!
    await retry.trigger('click')
    await flushPromises()
    expect(uploadImage).toHaveBeenCalledTimes(2)
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '移除')!
      .trigger('click')
    expect(wrapper.findAll('.pending-image')).toHaveLength(0)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
  })
  it('keeps attachments across an unsupported model switch and blocks sending', async () => {
    const wrapper = panel()
    await choose(wrapper)
    await wrapper.setProps({ modelFeatures: [] })
    expect(wrapper.text()).toContain('请移除图片或切换模型')
    await wrapper.get('form').trigger('submit')
    expect(ssePost).not.toHaveBeenCalled()
    expect(wrapper.findAll('.pending-image')).toHaveLength(1)
    await wrapper.setProps({ modelFeatures: ['image_input'] })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(ssePost).toHaveBeenCalledTimes(1)
  })
  it.each(['network', 'error', 'timeout'])(
    'restores the draft after %s failure',
    async (failure) => {
      vi.mocked(ssePost).mockImplementation(async (_, __, callback) => {
        if (failure === 'network') throw new Error('network')
        callback({ event: failure, data: { observation: 'failed' } })
      })
      const wrapper = panel()
      await choose(wrapper)
      await wrapper.get('textarea').setValue('describe')
      await wrapper.get('form').trigger('submit')
      await flushPromises()
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('describe')
      expect(wrapper.findAll('.pending-image')).toHaveLength(1)
    },
  )
  it('renders images loaded from history', async () => {
    vi.mocked(get).mockResolvedValue({
      data: {
        list: [{ id: 'old', query: '', image_urls: [url], answer: 'answer', agent_thoughts: [] }],
        paginator: {},
      },
    })
    const wrapper = panel()
    await flushPromises()
    expect(wrapper.find('.human-bubble img').attributes('src')).toBe(url)
  })
  it('renders an unavailable placeholder on image errors', async () => {
    const wrapper = mount(ChatImages, { props: { urls: [url] }, global })
    wrappers.push(wrapper)
    await wrapper.get('img').trigger('error')
    expect(wrapper.text()).toContain('图片加载失败')
  })
})

it('accepts five images and prevents uploading a sixth', async () => {
  const wrapper = panel()
  for (let count = 0; count < 6; count++) await choose(wrapper)
  expect(uploadImage).toHaveBeenCalledTimes(5)
  expect(wrapper.findAll('.pending-image')).toHaveLength(5)
  expect(wrapper.text()).toContain('最多5张图片')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(ssePost).toHaveBeenCalledWith(
    '/chat',
    expect.objectContaining({
      body: { query: '', image_urls: Array(5).fill(url) },
    }),
    expect.any(Function),
  )
})

it('uses the image_input feature, not the image_urls request field, as capability', async () => {
  const wrapper = panel(false)
  await wrapper.setProps({ modelFeatures: ['tool_call', 'image_urls'] })
  await choose(wrapper)
  expect(uploadImage).not.toHaveBeenCalled()
  await wrapper.setProps({ modelFeatures: ['tool_call', 'image_input'] })
  await choose(wrapper)
  expect(uploadImage).toHaveBeenCalledTimes(1)
})
