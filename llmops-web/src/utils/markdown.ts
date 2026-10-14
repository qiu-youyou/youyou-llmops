import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  highlight(code, language) {
    if (language && hljs.getLanguage(language)) {
      return hljs.highlight(code, { language, ignoreIllegals: true }).value
    }
    // 空字符串让 markdown-it 转义未知语言和未指定语言的代码。
    return ''
  },
})

export function renderMarkdown(content: string): string {
  return markdown.render(content)
}
