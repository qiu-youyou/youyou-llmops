// @vitest-environment jsdom
import { expect, it } from 'vitest'
import { renderMarkdown } from '../src/utils/markdown'

function render(content: string) {
  const element = document.createElement('div')
  element.innerHTML = renderMarkdown(content)
  return element
}

it('renders headings, emphasis, lists, quotes, tables and links', () => {
  const element = render(
    '# 标题\n\n**加粗** 和 `code`\n\n- 项目\n\n> 引用\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nhttps://example.com\n下一行',
  )
  expect(element.querySelector('h1')?.textContent).toBe('标题')
  expect(element.querySelector('strong')?.textContent).toBe('加粗')
  expect(element.querySelector('code')?.textContent).toBe('code')
  expect(element.querySelector('li')?.textContent).toBe('项目')
  expect(element.querySelector('blockquote')?.textContent).toContain('引用')
  expect(element.querySelectorAll('td')).toHaveLength(2)
  expect(element.querySelector('a')?.getAttribute('href')).toBe('https://example.com')
  expect(element.querySelector('br')).not.toBeNull()
})

it('escapes raw HTML and rejects executable link protocols', () => {
  const element = render(
    '<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[x](javascript:alert%281%29)\n\n[x](data:text/html;base64,PHNjcmlwdD4=)',
  )
  expect(element.querySelector('script, img, a')).toBeNull()
  expect(element.textContent).toContain('<script>alert(1)</script>')
})

it('highlights known languages and escapes unknown code languages', () => {
  const known = render('```javascript\nconst value = "<img>"\n```')
  expect(known.querySelector('.hljs-keyword')).not.toBeNull()
  expect(known.querySelector('img')).toBeNull()
  const unknown = render('```unknown-language\n<script>alert(1)</script>\n```')
  expect(unknown.querySelector('pre code')?.textContent).toBe('<script>alert(1)</script>\n')
  expect(unknown.querySelector('script')).toBeNull()
})

it('renders empty answers and partial code fences during streaming', () => {
  expect(renderMarkdown('')).toBe('')
  const partial = '```python\nprint("hello")'
  expect(render(partial).querySelector('pre code')?.textContent).toBe('print("hello")')
  const completed = render(partial + '\n```\n\n**完成**')
  expect(completed.querySelector('pre code')?.textContent).toBe('print("hello")\n')
  expect(completed.querySelector('strong')?.textContent).toBe('完成')
})
