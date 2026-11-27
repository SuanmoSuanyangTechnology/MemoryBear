/**
 * Resolve the project's file-type icon (src/assets/images/file) by file extension.
 * Classes are written as full literals so Tailwind can statically detect the background-image utilities.
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

/** Return the icon class by extension (falling back to the file name); unknown types use the txt icon */
export const getFileIconClassByExtension = (fileExt?: string, fileName?: string): string => {
  const extension = (fileExt || fileName?.split('.').pop() || '')
    .toLowerCase()
    .replace(/^\./, '')
    .split('/')
    .pop() ?? '';
  return FILE_ICON_CLASSES[EXTENSION_ICON_TYPES[extension] ?? 'text'];
};

/**
 * Resolve the icon type from MIME, following the `includes` matching rules of
 * `getFileIconType` in components/Chat/utils so long vendor MIME values are recognized.
 */
const getFileIconTypeByMime = (mimeType?: string): keyof typeof FILE_ICON_CLASSES | undefined => {
  const type = (mimeType || '').toLowerCase();

  if (type.includes('audio')) return 'audio';
  if (type.includes('video')) return 'video';
  if (type.includes('image')) return 'image';
  if (type.includes('pdf')) return 'pdf';
  if (type.includes('excel') || type.includes('spreadsheetml.sheet') || type.includes('xls')) return 'excel';
  if (type.includes('csv')) return 'csv';
  if (type.includes('html')) return 'html';
  if (type.includes('json')) return 'json';
  if (type.includes('ppt')) return 'ppt';
  if (type.includes('markdown')) return 'markdown';
  if (type.includes('text') && !type.includes('document')) return 'text';
  if (type.includes('doc') || type.includes('word') || type.includes('wordprocessingml.document')) return 'word';

  return undefined;
};

/** Resolve the icon class from a File's MIME type, with extension fallback; unknown types use the txt icon */
export const getFileIconClassByMimeType = (mimeType?: string, fileName?: string): string => {
  const extension = (fileName?.split('.').pop() || '').toLowerCase();
  const iconType = getFileIconTypeByMime(mimeType) ?? EXTENSION_ICON_TYPES[extension] ?? 'text';
  return FILE_ICON_CLASSES[iconType];
};
