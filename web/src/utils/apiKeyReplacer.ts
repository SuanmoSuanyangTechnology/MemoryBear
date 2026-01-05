/**
 * API密钥替换工具
 */

const API_KEY_PATTERNS = {
  service: /sk-service-[A-Za-z0-9_-]+/g,
  agent: /sk-agent-[A-Za-z0-9_-]+/g,
  multiAgent: /sk-multi_agent-[A-Za-z0-9_-]+/g,
  workflow: /sk-workflow-[A-Za-z0-9_-]+/g
}
const API_KEY_PREFIX = {
  service: 'sk-service-',
  agent: 'sk-agent-',
  multiAgent: 'sk-multi_agent-',
  workflow: 'sk-workflow-'
}

/**
 * 替换文本中的API密钥为*号
 * @param text 原始文本
 * @returns 替换后的文本
 */
export const maskApiKeys = (text: string): string => {
  if (!text) return text
  let result = text

  Object.keys(API_KEY_PREFIX).map(type => {
    const key = type as keyof typeof API_KEY_PREFIX
    result = result.replace(API_KEY_PATTERNS[key as keyof typeof API_KEY_PREFIX], (match) => {
      const prefixLength = API_KEY_PREFIX[key].length
      const prefix = match.substring(0, prefixLength)
      return prefix + '*'.repeat(match.length - prefixLength)
    })
  })

  return result
}

/**
 * 检测文本中是否包含API密钥
 * @param text 待检测文本
 * @returns 是否包含API密钥
 */
export const hasApiKeys = (text: string): boolean => {
  return Object.values(API_KEY_PATTERNS).some(pattern => pattern.test(text))
}