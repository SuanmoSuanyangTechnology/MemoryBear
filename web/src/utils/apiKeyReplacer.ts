/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:34:04 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:34:04 
 */
/**
 * API Key Replacer Utility
 * 
 * Provides functions to mask and detect API keys in text for security purposes.
 * Supports multiple API key formats (service, agent, multi-agent, workflow).
 * 
 * @module apiKeyReplacer
 */

/** API key pattern definitions for different types */
const API_KEY_PATTERNS = {
  service: /sk-service-[A-Za-z0-9_-]+/g,
  agent: /sk-agent-[A-Za-z0-9_-]+/g,
  multiAgent: /sk-multi_agent-[A-Za-z0-9_-]+/g,
  workflow: /sk-workflow-[A-Za-z0-9_-]+/g
}

/** API key prefix definitions */
const API_KEY_PREFIX = {
  service: 'sk-service-',
  agent: 'sk-agent-',
  multiAgent: 'sk-multi_agent-',
  workflow: 'sk-workflow-'
}

/**
 * Replace API keys in text with asterisks
 * @param text - Original text
 * @returns Text with masked API keys
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
 * Detect if text contains API keys
 * @param text - Text to check
 * @returns Whether text contains API keys
 */
export const hasApiKeys = (text: string): boolean => {
  return Object.values(API_KEY_PATTERNS).some(pattern => pattern.test(text))
}
