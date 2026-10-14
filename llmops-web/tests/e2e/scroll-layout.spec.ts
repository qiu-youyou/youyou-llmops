import { test, expect } from '@playwright/test'
import { mock, appId } from './fixtures'

for (const width of [1440, 1024, 390]) {
  test(`API documentation scrolls to its final paragraph at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await mock(page)
    await page.goto('/openapi')
    const content = page.locator('.openapi-content')
    await expect(page.getByRole('heading', { name: '将 Youyou 接入你的产品' })).toBeVisible()
    expect(await content.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)
    await content.evaluate((el) => {
      el.scrollTop = el.scrollHeight
    })
    await expect(page.getByText('金额可能是字符串或 null。', { exact: false })).toBeInViewport()
    await expect(page.getByRole('link', { name: '快速开始' })).toBeInViewport()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })

  test(`long debug history stays inside the chat panel at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await mock(page)
    await page.route(`**/apps/${appId}/conversations/messages?*`, (route) =>
      route.fulfill({
        json: {
          code: 'success',
          data: {
            list: Array.from({ length: 20 }, (_, i) => ({
              id: `message-${i}`,
              query: `历史问题 ${i}`,
              answer: '这是一段较长的历史回答。\n\n'.repeat(30),
            })),
            paginator: { total_page: 1 },
          },
        },
      }),
    )
    await page.goto(`/space/apps/${appId}`)
    await expect(page.locator('.chat-turn')).toHaveCount(20)
    const preview = page.locator('.app-preview')
    expect(await preview.evaluate((el) => el.getBoundingClientRect().height)).toBeLessThanOrEqual(
      800,
    )
    const scroll = page.locator('.chat-scroll')
    expect(
      await scroll.evaluate((el) => el.scrollHeight > el.clientHeight && el.clientHeight > 0),
    ).toBe(true)
    if (width >= 768) {
      await expect(page.getByRole('textbox', { name: '输入消息' })).toBeInViewport()
      expect(
        await page.locator('.app-layout').evaluate((el) => el.scrollHeight <= el.clientHeight + 1),
      ).toBe(true)
    } else {
      await page.getByRole('textbox', { name: '输入消息' }).scrollIntoViewIfNeeded()
      await expect(page.getByRole('textbox', { name: '输入消息' })).toBeInViewport()
    }
    await scroll.evaluate((el) => {
      el.style.scrollBehavior = 'auto'
      el.scrollTop = 100
    })
    await expect.poll(() => scroll.evaluate((el) => el.scrollTop)).toBeGreaterThan(0)
  })
}
