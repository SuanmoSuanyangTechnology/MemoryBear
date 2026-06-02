import { Image, Flex } from 'antd'
import clsx from 'clsx'
import AudioPlayer from './AudioPlayer'
import VideoPlayer from './VideoPlayer'

const getFileUrl = (file: any) =>
  file.thumbUrl || file.url || (file.originFileObj ? URL.createObjectURL(file.originFileObj) : undefined)

const DOC_ICONS: [string[], string][] = [
  [['pdf'], "rb:bg-[url('@/assets/images/file/pdf.svg')]"],
  [['excel', 'spreadsheetml.sheet', 'xls', 'xlsx'], "rb:bg-[url('@/assets/images/file/excel.svg')]"],
  [['csv'], "rb:bg-[url('@/assets/images/file/csv.svg')]"],
  [['html'], "rb:bg-[url('@/assets/images/file/html.svg')]"],
  [['json'], "rb:bg-[url('@/assets/images/file/json.svg')]"],
  [['ppt'], "rb:bg-[url('@/assets/images/file/ppt.svg')]"],
  [['markdown'], "rb:bg-[url('@/assets/images/file/md.svg')]"],
  [['text'], "rb:bg-[url('@/assets/images/file/txt.svg')]"],
  [['doc', 'docx', 'word', 'wordprocessingml.document'], "rb:bg-[url('@/assets/images/file/word.svg')]"],
]

const getDocIcon = (parts: string[]) => {
  const match = DOC_ICONS.find(([keys]) => keys.some(k => parts.includes(k)))
  return match ? match[1] : "rb:bg-[url('@/assets/images/file/txt.svg')]"
}

interface MessageFilesProps {
  files: any[]
  contentClassNames?: string | Record<string, boolean>
  onDownload: (file: any) => void
}

const MessageFiles = ({ files, contentClassNames, onDownload }: MessageFilesProps) => {
  if (!files?.length) return null
  return (
    <Flex gap={8} vertical align="end" className="rb:mb-2!">
      {files.map((file) => {
        const key = file.url || file.uid || file.file_key
        if (file.type.includes('image')) {
          return (
            <div key={key} className={clsx('rb:inline-block rb:group rb:relative rb:rounded-lg', contentClassNames)}>
              <Image src={getFileUrl(file)} alt={file.name} className="rb:w-full rb:max-w-80 rb:rounded-lg rb:object-cover rb:cursor-pointer" />
            </div>
          )
        }
        if (file.type.includes('video')) {
          return (
            <div key={key} className="rb:w-50">
              <VideoPlayer src={getFileUrl(file)} />
            </div>
          )
        }
        if (file.type.includes('audio')) {
          return (
            <div key={key} className="rb:w-50">
              <AudioPlayer src={getFileUrl(file)} fileName={file.name} fileSize={file.size} />
            </div>
          )
        }
        const documentType = (file.file_type || file.type)?.split('/') ?? []
        return (
          <Flex
            key={key}
            align="center"
            gap={10}
            className="rb:text-left rb:w-45 rb:text-[12px] rb:group rb:relative rb:rounded-lg rb-border rb:py-2! rb:px-2.5! rb:border rb:border-[#F6F6F6]"
            onClick={() => onDownload(file)}
          >
            <div
              className={clsx(
                "rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/pdf_disabled.svg')]",
                getDocIcon(documentType)
              )}
            />
            <div className="rb:flex-1 rb:w-32.5">
              <div className="rb:leading-4 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{file.name}</div>
              <div className="rb:leading-3.5 rb:mt-0.5 rb:text-[#5B6167] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">
                {documentType?.[documentType.length - 1]} · {file.size}
              </div>
            </div>
          </Flex>
        )
      })}
    </Flex>
  )
}

export default MessageFiles
