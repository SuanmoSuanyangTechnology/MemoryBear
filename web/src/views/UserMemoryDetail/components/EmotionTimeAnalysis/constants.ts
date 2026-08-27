export const EMOTION_COLORS: Readonly<Record<string, string>> = {
  anxiety: '#E98C4C',
  relief: '#4DA6A3',
  hope: '#4FA274',
  joy: '#EEB342',
  confusion: '#8978BD',
  neutral: '#98A2B1',
  loneliness: '#6575A2',
  frustration: '#A56C5B',
  anger: '#E26060',
  sadness: '#5989BD',
}

export const EMOTION_COLOR_CLASSES: Readonly<Record<string, string>> = {
  anxiety: 'rb:bg-[#E98C4C]',
  relief: 'rb:bg-[#4DA6A3]',
  hope: 'rb:bg-[#4FA274]',
  joy: 'rb:bg-[#EEB342]',
  confusion: 'rb:bg-[#8978BD]',
  neutral: 'rb:bg-[#98A2B1]',
  loneliness: 'rb:bg-[#6575A2]',
  frustration: 'rb:bg-[#A56C5B]',
  anger: 'rb:bg-[#E26060]',
  sadness: 'rb:bg-[#5989BD]',
}

export const UNKNOWN_EMOTION_COLOR = '#B8BEC7'
export const UNKNOWN_EMOTION_COLOR_CLASS = 'rb:bg-[#B8BEC7]'

export const emotionColor = (type: string) =>
  EMOTION_COLORS[type.trim().toLowerCase()] ?? UNKNOWN_EMOTION_COLOR

export const emotionColorClass = (type: string) =>
  EMOTION_COLOR_CLASSES[type.trim().toLowerCase()] ?? UNKNOWN_EMOTION_COLOR_CLASS
