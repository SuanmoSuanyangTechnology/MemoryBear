/**
 * 按文件扩展名获取项目内的文件类型图标（src/assets/images/file）。
 * class 均为完整字面量，确保 Tailwind 能静态扫描到背景图工具类。
 */
const FILE_ICON_CLASSES = {
  audio: "rb:bg-[url('@/assets/images/file/audio.svg')]",
  video: "rb:bg-[url('@/assets/images/file/video.svg')]",
  pdf: "rb:bg-[url('@/assets/images/file/pdf.svg')]",
  excel: "rb:bg-[url('@/assets/images/file/excel.svg')]",
  csv: "rb:bg-[url('@/assets/images/file/csv.svg')]",
  html: "rb:bg-[url('@/assets/images/file/html.svg')]",
  json: "rb:bg-[url('@/assets/images/file/json.svg')]",
  ppt: "rb:bg-[url('@/assets/images/file/ppt.svg')]",
  markdown: "rb:bg-[url('@/assets/images/file/md.svg')]",
  text: "rb:bg-[url('@/assets/images/file/txt.svg')]",
  word: "rb:bg-[url('@/assets/images/file/word.svg')]",
  image: "rb:bg-[url('@/assets/images/file/image.svg')]",
} as const;

const EXTENSION_ICON_TYPES: Record<string, keyof typeof FILE_ICON_CLASSES> = {
  pdf: 'pdf',
  doc: 'word', docx: 'word',
  xls: 'excel', xlsx: 'excel',
  csv: 'csv',
  ppt: 'ppt', pptx: 'ppt',
  md: 'markdown', markdown: 'markdown',
  txt: 'text',
  json: 'json',
  html: 'html', htm: 'html',
  mp3: 'audio', wav: 'audio', m4a: 'audio', flac: 'audio', aac: 'audio', ogg: 'audio',
  mp4: 'video', mov: 'video', avi: 'video', mkv: 'video', webm: 'video', wmv: 'video', flv: 'video',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', bmp: 'image', svg: 'image',
};

/** 根据扩展名（缺失时从文件名兜底）返回文件类型图标 class，未知类型回退到 txt */
export const getFileIconClassByExtension = (fileExt?: string, fileName?: string): string => {
  const extension = (fileExt || fileName?.split('.').pop() || '')
    .toLowerCase()
    .replace(/^\./, '')
    .split('/')
    .pop() ?? '';
  return FILE_ICON_CLASSES[EXTENSION_ICON_TYPES[extension] ?? 'text'];
};
