/*
 * cURL 字符串解析工具
 * 参考 dify.ts 的结构化写法：正则分词 + 各 flag 独立处理函数 + 返回 { data, error }。
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

/** 解析错误：key 为 i18n 文案键，values 为插值变量（如具体的 flag 名）。 */
export interface ParseCurlError {
  key: string;
  values?: Record<string, string>;
}

/** 解析结果：data 为解析后的结构，error 为格式校验错误（可国际化）。 */
export type ParseCurlResult = { data: ParsedCurl | null; error: ParseCurlError | null }

/** 各 flag 处理后的步进结果。 */
type ParseStepResult = {
  error: ParseCurlError | null;
  /** 已消费到的 token 下标（供主循环 i 直接赋值）。 */
  nextIndex: number;
}

/** 携带解析过程中可变状态的上下文。 */
interface ParseContext {
  result: ParsedCurl;
  headers: KeyValueRow[];
  dataParts: string[];
  formParts: KeyValueRow[];
  explicitMethod?: string;
  hasBody: boolean;
}

const METHOD_ARG_FLAGS = new Set(['-X', '--request'])
const HEADER_ARG_FLAGS = new Set(['-H', '--header'])
const DATA_ARG_FLAGS = new Set(['-d', '--data', '--data-raw', '--data-ascii', '--data-binary', '--data-urlencode'])
const FORM_ARG_FLAGS = new Set(['-F', '--form'])
const USER_AGENT_FLAGS = new Set(['-A', '--user-agent'])
const REFERER_FLAGS = new Set(['-e', '--referer'])
const COOKIE_FLAGS = new Set(['-b', '--cookie'])
const USER_FLAGS = new Set(['-u', '--user'])
const GET_FLAGS = new Set(['-G', '--get'])


/** 去掉字符串首尾成对的引号。 */
const stripWrappedQuotes = (value: string) => value.replace(/^['"]|['"]$/g, '')

/**
 * 将 cURL 命令切分为参数数组。
 * 正则可正确保留单/双引号包裹的整段值；多行续行的 `\` 会成为孤立 token，后续被忽略。
 */
const parseCurlArgs = (curlCommand: string): string[] => {
  return curlCommand.match(/(?:[^\s"']|"[^"]*"|'[^']*')+/g) || []
}

/** 将 `key: value` 形式的头部字符串拆分为键值对。 */
const splitHeader = (raw: string): KeyValueRow | null => {
  const index = raw.indexOf(':')
  if (index === -1) return null
  const key = raw.slice(0, index).trim()
  const value = raw.slice(index + 1).trim()
  if (!key) return null
  return { key, value }
}

/** 取出 index 后的下一个参数（已去引号），缺失时返回校验错误。 */
const getNextArg = (
  args: string[],
  index: number,
  error: ParseCurlError,
): { value: string; error: null } | { value: null; error: ParseCurlError } => {
  if (index + 1 >= args.length) return { value: null, error }
  return { value: stripWrappedQuotes(args[index + 1]), error: null }
}

/** 构造「某 flag 后缺少对应值」的国际化错误。 */
const missingArgError = (flag: string): ParseCurlError => ({
  key: 'workflow.config.http-request.curlErrMissingArg',
  values: { flag },
})

/** -X / --request：显式请求方法。 */
const applyMethodArg = (ctx: ParseContext, args: string[], index: number): ParseStepResult => {
  const next = getNextArg(args, index, missingArgError('-X / --request'))
  if (next.error !== null) return { error: next.error, nextIndex: index }
  // 去掉续行反斜杠等非字母字符，避免 method 带上 `\`（如 `get\` → `GET`）
  ctx.explicitMethod = next.value.replace(/[^a-zA-Z]/g, '').toUpperCase()
  return { error: null, nextIndex: index + 1 }
}

/** -H / --header：请求头，仅忽略 Cookie。 */
const applyHeaderArg = (ctx: ParseContext, args: string[], index: number): ParseStepResult => {
  const next = getNextArg(args, index, missingArgError('-H / --header'))
  if (next.error !== null) return { error: next.error, nextIndex: index }
  const header = splitHeader(next.value)
  if (header) ctx.headers.push(header)
  return { error: null, nextIndex: index + 1 }
}

/** -A / --user-agent：作为 User-Agent 请求头保留。 */
const applyUserAgentArg = (ctx: ParseContext, args: string[], index: number): ParseStepResult => {
  const next = getNextArg(args, index, missingArgError('-A / --user-agent'))
  if (next.error !== null) return { error: next.error, nextIndex: index }
  ctx.headers.push({ key: 'User-Agent', value: next.value })
  return { error: null, nextIndex: index + 1 }
}

/** -e / --referer：作为 Referer 请求头保留。 */
const applyRefererArg = (ctx: ParseContext, args: string[], index: number): ParseStepResult => {
  const next = getNextArg(args, index, missingArgError('-e / --referer'))
  if (next.error !== null) return { error: next.error, nextIndex: index }
  ctx.headers.push({ key: 'Referer', value: next.value })
  return { error: null, nextIndex: index + 1 }
}

/** -b / --cookie：忽略取值（但仍校验其后有值）。 */
const applyCookieArg = (_ctx: ParseContext, args: string[], index: number): ParseStepResult => {
  const next = getNextArg(args, index, missingArgError('-b / --cookie'))
  if (next.error !== null) return { error: next.error, nextIndex: index }
  return { error: null, nextIndex: index + 1 }
}

/** -d / --data* / --json：请求体数据，统一累积后归为 raw。 */
const applyDataArg = (
  ctx: ParseContext,
  args: string[],
  index: number,
  label: string,
): ParseStepResult => {
  const next = getNextArg(args, index, missingArgError(label))
  if (next.error !== null) return { error: next.error, nextIndex: index }
  ctx.dataParts.push(next.value)
  ctx.hasBody = true
  return { error: null, nextIndex: index + 1 }
}

/** -F / --form：表单字段，支持 `value;type=<mime>` 提取 Content-Type。 */
const applyFormArg = (ctx: ParseContext, args: string[], index: number): ParseStepResult => {
  const next = getNextArg(args, index, missingArgError('-F / --form'))
  if (next.error !== null) return { error: next.error, nextIndex: index }

  const eqIndex = next.value.indexOf('=')
  if (eqIndex === -1) return { error: { key: 'workflow.config.http-request.curlErrInvalidForm' }, nextIndex: index }

  const key = next.value.slice(0, eqIndex).trim()
  let value = next.value.slice(eqIndex + 1).trim()

  const typeMatch = /^(.+?);type=(.+)$/.exec(value)
  if (typeMatch) {
    value = typeMatch[1]
    ctx.headers.push({ key: 'Content-Type', value: typeMatch[2] })
  }

  ctx.formParts.push({ key, value })
  ctx.hasBody = true
  return { error: null, nextIndex: index + 1 }
}

/** -u / --user：基础鉴权 user:password。 */
const applyUserArg = (ctx: ParseContext, args: string[], index: number): ParseStepResult => {
  const next = getNextArg(args, index, missingArgError('-u / --user'))
  if (next.error !== null) return { error: next.error, nextIndex: index }
  ctx.result.auth = { auth_type: 'basic', api_key: next.value }
  return { error: null, nextIndex: index + 1 }
}

/** 单个 token 的分发处理。 */
const handleCurlArg = (
  arg: string,
  ctx: ParseContext,
  args: string[],
  index: number,
): ParseStepResult => {
  if (METHOD_ARG_FLAGS.has(arg)) return applyMethodArg(ctx, args, index)
  if (HEADER_ARG_FLAGS.has(arg)) return applyHeaderArg(ctx, args, index)
  if (USER_AGENT_FLAGS.has(arg)) return applyUserAgentArg(ctx, args, index)
  if (REFERER_FLAGS.has(arg)) return applyRefererArg(ctx, args, index)
  if (COOKIE_FLAGS.has(arg)) return applyCookieArg(ctx, args, index)
  if (DATA_ARG_FLAGS.has(arg)) return applyDataArg(ctx, args, index, arg)
  if (arg === '--json') return applyDataArg(ctx, args, index, '--json')
  if (FORM_ARG_FLAGS.has(arg)) return applyFormArg(ctx, args, index)
  if (USER_FLAGS.has(arg)) return applyUserArg(ctx, args, index)
  if (GET_FLAGS.has(arg)) {
    ctx.explicitMethod = 'GET'
    return { error: null, nextIndex: index }
  }

  // 其余：以 http(s) 开头且尚无 url 时视为 url；带值的未知选项保守跳过其值
  if (/^https?:\/\//i.test(arg) && !ctx.result.url) ctx.result.url = arg
  return { error: null, nextIndex: index }
}

/** 从 url 中拆分 query，转成 params 键值对。 */
const extractParams = (url: string): { url: string; params: KeyValueRow[] } => {
  const questionIndex = url.indexOf('?')
  if (questionIndex === -1) return { url, params: [] }

  const params: KeyValueRow[] = []
  url.slice(questionIndex + 1).split('&').forEach(pair => {
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

  return { url: url.slice(0, questionIndex), params }
}

/**
 * 解析 cURL 字符串，并对其格式进行校验。
 * 校验项：必须以 "curl" 开头、各 flag 后需有对应值、URL 必须存在且以 http(s) 开头。
 * 解析失败时 data 为 null，error 为具体错误信息。
 */
export const parseCurl = (curl: string): ParseCurlResult => {
  if (!curl || !curl.trim()) return { data: null, error: { key: 'workflow.config.http-request.curlErrEmpty' } }

  const trimmed = curl.trim()
  if (!trimmed.toLowerCase().startsWith('curl')) {
    return { data: null, error: { key: 'workflow.config.http-request.curlErrNotCurl' } }
  }

  const args = parseCurlArgs(trimmed)
  if (args.length === 0) return { data: null, error: { key: 'workflow.config.http-request.curlErrInvalid' } }

  const ctx: ParseContext = {
    result: {},
    headers: [],
    dataParts: [],
    formParts: [],
    hasBody: false,
  }

  // 从下标 1 开始，跳过起始的 curl 关键字
  for (let i = 1; i < args.length; i++) {
    const step = handleCurlArg(stripWrappedQuotes(args[i]).trim(), ctx, args, i)
    if (step.error) return { data: null, error: step.error }
    i = step.nextIndex
  }

  const { result, headers, dataParts, formParts } = ctx

  if (!result.url) return { data: null, error: { key: 'workflow.config.http-request.curlErrNoUrl' } }

  // 拆分 url 中的 query 作为 params
  const { url, params } = extractParams(result.url)
  result.url = url
  if (params.length > 0) result.params = params

  // Authorization 保留在 headers 中，不再抽取为独立鉴权配置
  if (headers.length > 0) result.headers = headers

  // 归类请求方法：显式优先，否则有 body 时默认 POST，其余 GET
  result.method = ctx.explicitMethod || (ctx.hasBody ? 'POST' : 'GET')

  // 归类 body：form-data 单独处理，其余（-d/--data 等）一律作为 raw
  if (formParts.length > 0) {
    result.body = { content_type: 'form-data', data: formParts }
  } else if (dataParts.length > 0) {
    result.body = { content_type: 'raw', data: dataParts.join('&') }
  }

  return { data: result, error: null }
}
