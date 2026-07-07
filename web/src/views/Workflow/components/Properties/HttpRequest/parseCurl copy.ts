/*
 * cURL 字符串解析工具
 * 将粘贴的 cURL 命令解析为 HttpRequest 节点表单可直接回填的结构。
 */
import type { HttpRequestConfigForm } from './types'

/** 表单中 headers / params / form-data 行的数据结构。 */
export interface KeyValueRow {
  key: string;
  value: string;
  type?: string;
}

/** cURL 解析结果，字段与 HttpRequest 表单一一对应。 */
export interface ParsedCurl {
  method?: string;
  url?: string;
  headers?: KeyValueRow[];
  params?: KeyValueRow[];
  body?: {
    content_type: string;
    data: string | KeyValueRow[];
  };
  auth?: HttpRequestConfigForm['auth'];
}

/**
 * 将 cURL 命令按 shell 词法切分为参数数组。
 * 支持单引号 / 双引号包裹、反斜杠续行、以及 `$'...'` 形式。
 */
const tokenize = (input: string): string[] => {
  // 去掉行尾用于续行的反斜杠，使多行 cURL 合并为一行
  const normalized = input.replace(/\\\r?\n/g, ' ')
  const tokens: string[] = []
  let current = ''
  let quote: '"' | "'" | null = null
  let hasToken = false

  for (let i = 0; i < normalized.length; i++) {
    const char = normalized[i]

    if (quote) {
      if (char === quote) {
        quote = null
      } else if (char === '\\' && quote === '"') {
        // 双引号内支持转义
        const next = normalized[i + 1]
        if (next !== undefined) {
          current += next
          i++
        }
      } else {
        current += char
      }
      continue
    }

    if (char === '"' || char === "'") {
      quote = char
      hasToken = true
      continue
    }

    if (char === '\\') {
      const next = normalized[i + 1]
      if (next !== undefined) {
        current += next
        i++
        hasToken = true
      }
      continue
    }

    if (/\s/.test(char)) {
      if (hasToken) {
        tokens.push(current)
        current = ''
        hasToken = false
      }
      continue
    }

    current += char
    hasToken = true
  }

  if (hasToken) tokens.push(current)
  return tokens
}

/** 将 `key: value` 或 `key=value` 形式的头部字符串拆分为键值对。 */
const splitHeader = (raw: string): KeyValueRow | null => {
  const index = raw.indexOf(':')
  if (index === -1) return null
  const key = raw.slice(0, index).trim()
  const value = raw.slice(index + 1).trim()
  if (!key) return null
  return { key, value }
}

/** 解析时忽略的请求头（仅 Cookie，其余请求头一律保留）。 */
const IGNORED_HEADERS = ['cookie']

/** 判断某个请求头是否需要被忽略。 */
const isIgnoredHeader = (key: string) => IGNORED_HEADERS.includes(key.trim().toLowerCase())

/** body 类型到默认 Content-Type 头值的映射，用于自动补齐。 */
const BODY_CONTENT_TYPE: Record<string, string> = {
  json: 'application/json',
  'x-www-form-urlencoded': 'application/x-www-form-urlencoded',
  'form-data': 'multipart/form-data',
  raw: 'text/plain',
  binary: 'application/octet-stream',
}

/** 从 URL 中提取 Origin（协议 + 域名 + 端口）。 */
const getOrigin = (url: string): string => {
  const match = url.match(/^([a-zA-Z][\w+.-]*:\/\/[^/?#]+)/)
  return match ? match[1] : ''
}

/**
 * 解析 cURL 字符串。解析失败（无有效 url）时返回 null。
 */
export const parseCurl = (curl: string): ParsedCurl | null => {
  if (!curl || !curl.trim()) return null

  const tokens = tokenize(curl.trim())
  if (tokens.length === 0) return null

  // 跳过起始的 curl 关键字
  let start = 0
  if (tokens[0] === 'curl') start = 1

  const result: ParsedCurl = {}
  const headers: KeyValueRow[] = []
  const dataParts: string[] = []
  const formParts: KeyValueRow[] = []
  let explicitMethod: string | undefined
  let hasBody = false

  for (let i = start; i < tokens.length; i++) {
    const token = tokens[i]

    switch (token) {
      case '-X':
      case '--request':
        explicitMethod = tokens[++i]?.toUpperCase()
        break
      case '-H':
      case '--header': {
        const header = splitHeader(tokens[++i] ?? '')
        // 仅忽略 Cookie，其余请求头一律保留
        if (header && !isIgnoredHeader(header.key)) headers.push(header)
        break
      }
      case '-A':
      case '--user-agent':
        headers.push({ key: 'User-Agent', value: tokens[++i] ?? '' })
        break
      case '-e':
      case '--referer':
        headers.push({ key: 'Referer', value: tokens[++i] ?? '' })
        break
      case '-b':
      case '--cookie':
        // 忽略 Cookie
        i++
        break
      case '-d':
      case '--data':
      case '--data-raw':
      case '--data-ascii':
      case '--data-binary':
        dataParts.push(tokens[++i] ?? '')
        hasBody = true
        break
      case '--data-urlencode': {
        dataParts.push(tokens[++i] ?? '')
        hasBody = true
        break
      }
      case '-F':
      case '--form': {
        const raw = tokens[++i] ?? ''
        const eqIndex = raw.indexOf('=')
        if (eqIndex !== -1) {
          formParts.push({ key: raw.slice(0, eqIndex).trim(), value: raw.slice(eqIndex + 1).trim() })
        }
        hasBody = true
        break
      }
      case '-u':
      case '--user': {
        // 基础鉴权 user:password
        const cred = tokens[++i] ?? ''
        result.auth = {
          auth_type: 'basic',
          api_key: cred,
        }
        break
      }
      case '-G':
      case '--get':
        explicitMethod = 'GET'
        break
      default: {
        // 未知参数：带值的长/短选项跳过其值；否则视为 url
        if (token.startsWith('-')) {
          // 形如 --key=value 的忽略；--key value 无法确定是否带值，保守跳过单独 token
          break
        }
        if (!result.url) {
          result.url = token
        }
        break
      }
    }
  }

  if (!result.url) return null

  // 拆分 url 中的 query 作为 params
  const params: KeyValueRow[] = []
  const questionIndex = result.url.indexOf('?')
  if (questionIndex !== -1) {
    const query = result.url.slice(questionIndex + 1)
    result.url = result.url.slice(0, questionIndex)
    query.split('&').forEach(pair => {
      if (!pair) return
      const eqIndex = pair.indexOf('=')
      const key = eqIndex === -1 ? pair : pair.slice(0, eqIndex)
      const value = eqIndex === -1 ? '' : pair.slice(eqIndex + 1)
      try {
        params.push({ key: decodeURIComponent(key), value: decodeURIComponent(value) })
      } catch {
        params.push({ key, value })
      }
    })
  }
  if (params.length > 0) result.params = params

  // Authorization 保留在 headers 中，不再抽取为独立鉴权配置
  if (headers.length > 0) result.headers = headers

  // 归类请求方法：显式优先，否则有 body 时默认 POST，其余 GET
  result.method = explicitMethod || (hasBody ? 'POST' : 'GET')

  // 归类 body：优先 form-data，其次按 Content-Type 判断 json / urlencoded / raw
  if (formParts.length > 0) {
    result.body = { content_type: 'form-data', data: formParts }
  } else if (dataParts.length > 0) {
    const rawData = dataParts.join('&')
    const contentType = (result.headers || [])
      .find(h => h.key.toLowerCase() === 'content-type')?.value?.toLowerCase() || ''

    if (contentType.includes('application/json') || /^\s*[[{]/.test(rawData)) {
      result.body = { content_type: 'json', data: rawData }
    } else if (contentType.includes('x-www-form-urlencoded') || (rawData.includes('=') && !rawData.includes('\n'))) {
      const rows: KeyValueRow[] = []
      rawData.split('&').forEach(pair => {
        if (!pair) return
        const eqIndex = pair.indexOf('=')
        const key = eqIndex === -1 ? pair : pair.slice(0, eqIndex)
        const value = eqIndex === -1 ? '' : pair.slice(eqIndex + 1)
        rows.push({ key: key.trim(), value: value.trim() })
      })
      result.body = { content_type: 'x-www-form-urlencoded', data: rows }
    } else {
      result.body = { content_type: 'raw', data: rawData }
    }
  }

  // 补齐 Content-Type、Origin：cURL 中已提供则保留原值，缺失时自动补充
  const finalHeaders = result.headers ?? []
  const hasHeader = (name: string) => finalHeaders.some(h => h.key.trim().toLowerCase() === name)

  if (!hasHeader('content-type')) {
    // 缺失 Content-Type 时补齐：有 body 按 body 类型取值，否则默认 application/json
    const contentType = (result.body && BODY_CONTENT_TYPE[result.body.content_type]) || 'application/json'
    finalHeaders.push({ key: 'Content-Type', value: contentType })
  }
  if (!hasHeader('origin')) {
    const origin = getOrigin(result.url)
    if (origin) finalHeaders.push({ key: 'Origin', value: origin })
  }
  if (finalHeaders.length > 0) result.headers = finalHeaders

  return result
}
