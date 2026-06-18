const urlConfig = {
  help: (lang: 'zh' | 'en') => `https://docs.redbearai.com/s/${lang}-memorybear`,
  'memory-read': (lang: 'zh' | 'en') => `https://docs.redbearai.com/s/${lang}-memorybear/doc/6k6w5bg5oq5yw-qhmjyEHYOK`,
  'memory-write': (lang: 'zh' | 'en') => `https://docs.redbearai.com/s/${lang}-memorybear/doc/6k6w5bg5yko5a2y-BxNYN479dv`,
}
export const openHelpCenter = (currentLang: 'zh' | 'en', type: string | undefined = 'help') => {
  const lang = currentLang === 'zh' ? 'zh' : 'en';

  // 创建隐藏的 a 标签来避免弹窗拦截
  const link = document.createElement('a');
  link.href = urlConfig[type as keyof typeof urlConfig](lang);
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
};