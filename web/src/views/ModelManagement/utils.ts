/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:50:22 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-01 10:15:54
 */
/**
 * Utility functions for Model Management
 */
import type { OptionType } from '@/components/CustomSelect'

import bedrockIcon from '@/assets/images/model/bedrock.png'
import dashscopeIcon from '@/assets/images/model/dashscope.png'
import gpustackIcon from '@/assets/images/model/gpustack.png'
import ollamaIcon from '@/assets/images/model/ollama.png'
import openaiIcon from '@/assets/images/model/openai.png'
import xinferenceIcon from '@/assets/images/model/xinference.png'
import volcanoIcon from '@/assets/images/model/volcano.png'
import speedbearIcon from '@/assets/images/logo.png'

/**
 * Provider icon mapping
 */
export const ICONS = {
  bedrock: bedrockIcon,
  dashscope: dashscopeIcon,
  gpustack: gpustackIcon,
  ollama: ollamaIcon,
  openai: openaiIcon,
  xinference: xinferenceIcon,
  volcano: volcanoIcon,
  speedbear: speedbearIcon,
}

/**
 * Get logo URL from provider name or URL
 * @param logo - Provider name or logo URL
 * @returns Logo URL or undefined
 */
export const getLogoUrl = (logo?: string) => {
  if (!logo) {
    return undefined
  }
  if (logo.startsWith('http')) {
    return logo
  }

  return ICONS[logo as keyof typeof ICONS] || undefined
}

/**
 * Get logo URL from provider name or URL
 * @param provider - Provider name
 * @param logo - Provider name or logo URL
 * @returns Logo URL or undefined
 */
export const getListLogoUrl = (provider?: string, logo?: string) => {
  let url = ICONS[provider as keyof typeof ICONS]

  if (url) return url

  if (!logo) {
    return undefined
  }
  if (logo.startsWith('http')) {
    return logo
  }

  return ICONS[logo as keyof typeof ICONS] || undefined
}

export const formatModelType = (type: OptionType['value']) => {
  if (type === 'llm') {
    return 'LLM'
  }
  return type.charAt(0).toUpperCase() + type.slice(1)
}
