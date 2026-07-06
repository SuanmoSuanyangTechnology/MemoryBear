const urlConfig = {
  help: (lang: 'zh' | 'en') => `/docs/?lang=${lang}`,
  'memory-read': (lang: 'zh' | 'en') => `/docs/?lang=${lang}&page=u63`,
  'memory-write': (lang: 'zh' | 'en') => `/docs/?lang=${lang}&page=u64`,
  'api-key': (lang: 'zh' | 'en') => `/docs/?lang=${lang}&page=api-overview`,
}
export const openHelpCenter = (currentLang: 'zh' | 'en', type: string | undefined = 'help') => {
  const lang = currentLang === 'zh' ? 'zh' : 'en';

  window.open(urlConfig[type as keyof typeof urlConfig](lang), '_blank')
};