import type { UploadFile } from 'antd'

const DISABLED_FILE_CLASS_NAMES = {
  audio: "rb:bg-[url('@/assets/images/file/audio_disabled.svg')]",
  video: "rb:bg-[url('@/assets/images/file/video_disabled.svg')]",
  pdf: "rb:bg-[url('@/assets/images/file/pdf_disabled.svg')]",
  excel: "rb:bg-[url('@/assets/images/file/excel_disabled.svg')]",
  csv: "rb:bg-[url('@/assets/images/file/csv_disabled.svg')]",
  html: "rb:bg-[url('@/assets/images/file/html_disabled.svg')]",
  json: "rb:bg-[url('@/assets/images/file/json_disabled.svg')]",
  ppt: "rb:bg-[url('@/assets/images/file/ppt_disabled.svg')]",
  markdown: "rb:bg-[url('@/assets/images/file/md_disabled.svg')]",
  text: "rb:bg-[url('@/assets/images/file/txt_disabled.svg')]",
  word: "rb:bg-[url('@/assets/images/file/word_disabled.svg')]",
} as const

const FILE_CLASS_NAMES = {
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
} as const

type FileIconType = keyof typeof FILE_CLASS_NAMES

const getFileIconType = (file: UploadFile, includeExcelExtensions: boolean): FileIconType => {
  const type = file.type || ''

  if (type.includes('audio')) return 'audio'
  if (type.includes('video')) return 'video'
  if (type.includes('pdf')) return 'pdf'
  if (
    type.includes('excel')
    || type.includes('spreadsheetml.sheet')
    || (includeExcelExtensions && (type.includes('xls') || type.includes('xlsx')))
  ) return 'excel'
  if (type.includes('csv')) return 'csv'
  if (type.includes('html')) return 'html'
  if (type.includes('json')) return 'json'
  if (type.includes('ppt')) return 'ppt'
  if (type.includes('markdown')) return 'markdown'
  if (type.includes('text')) return 'text'
  if (
    type.includes('doc')
    || type.includes('docx')
    || type.includes('word')
    || type.includes('wordprocessingml.document')
  ) return 'word'

  return 'text'
}

export const getFileIconClassName = (file: UploadFile) => {
  const isUploading = file.status === 'uploading'
  const iconType = getFileIconType(file, !isUploading)
  const classNames = isUploading ? DISABLED_FILE_CLASS_NAMES : FILE_CLASS_NAMES

  return classNames[iconType]
}
