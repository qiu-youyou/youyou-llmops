// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, reactive } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useGetDocumentsWithPage } from '../src/hooks/use-dataset'

const { getDocumentsWithPage } = vi.hoisted(() => ({ getDocumentsWithPage: vi.fn() }))
const route = reactive({ query: { current_page: 1, search_word: '' } })
vi.mock('vue-router', () => ({ useRoute: () => route }))
vi.mock('@/services/dataset', () => ({ getDocumentsWithPage }))
vi.mock('@arco-design/web-vue', () => ({ Form: {}, Message: {}, Modal: {} }))

const response = (statuses: string[], page = 1) => ({
  data: {
    list: statuses.map((status, i) => ({ id: `${page}-${i}`, status, enabled: true })),
    paginator: { current_page: page, page_size: 20, total_page: 2, total_record: 21 },
  },
})

let wrapper: VueWrapper | undefined
let state: ReturnType<typeof useGetDocumentsWithPage>
const start = async () => {
  wrapper = mount(
    defineComponent({
      setup() {
        state = useGetDocumentsWithPage('dataset')
        return () => h('div')
      },
    }),
  )
  await flushPromises()
}

beforeEach(() => {
  vi.useFakeTimers()
  getDocumentsWithPage.mockReset()
  route.query = { current_page: 1, search_word: '' }
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.useRealTimers()
})

describe('document list polling', () => {
  it('refreshes waiting and indexing documents until every document completes or fails', async () => {
    getDocumentsWithPage
      .mockResolvedValueOnce(response(['indexing', 'waiting']))
      .mockResolvedValueOnce(response(['completed', 'indexing']))
      .mockResolvedValueOnce(response(['completed', 'error']))
    await start()
    expect(state.documents.map((d) => d.status)).toEqual(['indexing', 'waiting'])
    await vi.advanceTimersByTimeAsync(5000)
    expect(state.documents.map((d) => d.status)).toEqual(['completed', 'indexing'])
    await vi.advanceTimersByTimeAsync(5000)
    expect(state.documents.map((d) => d.status)).toEqual(['completed', 'error'])
    await vi.advanceTimersByTimeAsync(15000)
    expect(getDocumentsWithPage).toHaveBeenCalledTimes(3)
  })

  it('keeps existing rows during a background request and allows retry after failure', async () => {
    let reject!: (error: Error) => void
    getDocumentsWithPage
      .mockResolvedValueOnce(response(['indexing']))
      .mockImplementationOnce(
        () =>
          new Promise((_, rejectRequest) => {
            reject = rejectRequest
          }),
      )
      .mockResolvedValueOnce(response(['completed']))
    await start()
    await vi.advanceTimersByTimeAsync(5000)
    expect(state.loading.value).toBe(false)
    expect(state.documents[0].status).toBe('indexing')
    reject(new Error('网络连接失败'))
    await flushPromises()
    expect(state.pollingError.value).toBe('网络连接失败')
    await vi.advanceTimersByTimeAsync(10000)
    expect(getDocumentsWithPage).toHaveBeenCalledTimes(2)
    await state.loadDocuments(true)
    expect(state.pollingError.value).toBe('')
    expect(state.documents[0].status).toBe('completed')
  })

  it('ignores an old polling response after switching pages', async () => {
    let resolve!: (value: ReturnType<typeof response>) => void
    getDocumentsWithPage
      .mockResolvedValueOnce(response(['indexing']))
      .mockImplementationOnce(
        () =>
          new Promise((resolveRequest) => {
            resolve = resolveRequest
          }),
      )
      .mockResolvedValueOnce(response(['completed'], 2))
    await start()
    await vi.advanceTimersByTimeAsync(5000)
    route.query = { current_page: 2, search_word: '' }
    await flushPromises()
    expect(state.documents[0].id).toBe('2-0')
    resolve(response(['indexing']))
    await flushPromises()
    expect(state.documents[0].id).toBe('2-0')
    await vi.advanceTimersByTimeAsync(10000)
    expect(getDocumentsWithPage).toHaveBeenCalledTimes(3)
  })

  it('does not update rows or start another timer when an in-flight request finishes after unmount', async () => {
    let resolve!: (value: ReturnType<typeof response>) => void
    getDocumentsWithPage.mockResolvedValueOnce(response(['waiting'])).mockImplementationOnce(
      () =>
        new Promise((resolveRequest) => {
          resolve = resolveRequest
        }),
    )
    await start()
    await vi.advanceTimersByTimeAsync(5000)
    wrapper?.unmount()
    wrapper = undefined
    resolve(response(['indexing']))
    await flushPromises()
    expect(state.documents[0].status).toBe('waiting')
    await vi.advanceTimersByTimeAsync(10000)
    expect(getDocumentsWithPage).toHaveBeenCalledTimes(2)
  })
})
