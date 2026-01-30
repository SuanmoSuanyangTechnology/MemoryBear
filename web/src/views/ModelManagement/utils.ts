import bedrockIcon from '@/assets/images/model/bedrock.svg'
import dashscopeIcon from '@/assets/images/model/dashscope.png'
import gpustackIcon from '@/assets/images/model/gpustack.png'
import ollamaIcon from '@/assets/images/model/ollama.svg'
import openaiIcon from '@/assets/images/model/openai.svg'
import xinferenceIcon from '@/assets/images/model/xinference.svg'

export const ICONS = {
  bedrock: bedrockIcon,
  dashscope: dashscopeIcon,
  gpustack: gpustackIcon,
  ollama: ollamaIcon,
  openai: openaiIcon,
  xinference: xinferenceIcon
}

export const getLogoUrl = (logo?: string) => {
  if (!logo) {
    return undefined
  }
  if (logo.startsWith('http')) {
    return logo
  }

  return ICONS[logo as keyof typeof ICONS] || undefined
}