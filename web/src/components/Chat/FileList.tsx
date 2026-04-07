import { type FC, useRef, useState } from 'react'
import { Flex, Spin } from 'antd'
import { CloseOutlined } from '@ant-design/icons'
import clsx from 'clsx'
import type { UploadFile, FlexProps } from 'antd'

interface FileListProps {
  fileList: UploadFile[];
  onDelete?: (file: UploadFile) => void;
  wrap?: FlexProps['wrap'];
  className?: string;
}

const FileList: FC<FileListProps> = ({ fileList, onDelete, wrap,
  className = "rb:mx-3! rb:mt-3! rb:w-max!"
 }) => {
  const [playingUid, setPlayingUid] = useState<string | null>(null)
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement>(null)

  const handleClose = () => {
    mediaRef.current?.pause()
    setPlayingUid(null)
  }

  const playingFile = fileList.find(f => f.uid === playingUid)

  if (!fileList.length) return null

  const getFileIconClassName = (file: UploadFile) => {
    console.log('getFileIconClassName file', file)
    if (file.status === 'uploading') {
      return file.type?.includes('audio')
        ? "rb:bg-[url('@/assets/images/file/audio_disabled.svg')]"
        : file.type?.includes('video')
          ? "rb:bg-[url('@/assets/images/file/video_disabled.svg')]"
          : file.type?.includes('pdf')
            ? "rb:bg-[url('@/assets/images/file/pdf_disabled.svg')]"
            : (file.type?.includes('excel') || file.type?.includes('spreadsheetml.sheet'))
              ? "rb:bg-[url('@/assets/images/file/excel_disabled.svg')]"
              : file.type?.includes('csv')
                ? "rb:bg-[url('@/assets/images/file/csv_disabled.svg')]"
                : file.type?.includes('html')
                  ? "rb:bg-[url('@/assets/images/file/html_disabled.svg')]"
                  : file.type?.includes('json')
                    ? "rb:bg-[url('@/assets/images/file/json_disabled.svg')]"
                    : file.type?.includes('ppt')
                      ? "rb:bg-[url('@/assets/images/file/ppt_disabled.svg')]"
                      : file.type?.includes('markdown')
                        ? "rb:bg-[url('@/assets/images/file/md_disabled.svg')]"
                      : file.type?.includes('text')
                        ? "rb:bg-[url('@/assets/images/file/txt_disabled.svg')]"
                          : (file.type?.includes('doc') || file.type?.includes('docx') || file.type?.includes('word') || file.type?.includes('wordprocessingml.document'))
                            ? "rb:bg-[url('@/assets/images/file/word_disabled.svg')]"
                            : "rb:bg-[url('@/assets/images/file/txt_disabled.svg')]"
    }
    return file.type?.includes('audio')
      ? "rb:bg-[url('@/assets/images/file/audio.svg')]"
      : file.type?.includes('video')
        ? "rb:bg-[url('@/assets/images/file/video.svg')]"
        : file.type?.includes('pdf')
          ? "rb:bg-[url('@/assets/images/file/pdf.svg')]"
          : (file.type?.includes('excel') || file.type?.includes('spreadsheetml.sheet'))
            ? "rb:bg-[url('@/assets/images/file/excel.svg')]"
            : file.type?.includes('csv')
              ? "rb:bg-[url('@/assets/images/file/csv.svg')]"
              : file.type?.includes('html')
                ? "rb:bg-[url('@/assets/images/file/html.svg')]"
                : file.type?.includes('json')
                  ? "rb:bg-[url('@/assets/images/file/json.svg')]"
                  : file.type?.includes('ppt')
                    ? "rb:bg-[url('@/assets/images/file/ppt.svg')]"
                    : file.type?.includes('markdown')
                      ? "rb:bg-[url('@/assets/images/file/md.svg')]"
                    : file.type?.includes('text')
                      ? "rb:bg-[url('@/assets/images/file/txt.svg')]"
                        : (file.type?.includes('doc') || file.type?.includes('docx') || file.type?.includes('word') || file.type?.includes('wordprocessingml.document'))
                          ? "rb:bg-[url('@/assets/images/file/word.svg')]"
                          : "rb:bg-[url('@/assets/images/file/txt.svg')]"
  }

  return (
    <>
      <Flex gap={14} wrap={wrap} className={className}>
        {fileList.map((file) => {
          if (file.type?.includes('image')) {
            return (
              <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
                <div className={clsx("rb:inline-block rb:group rb:relative rb:rounded-lg rb:bg-[#F6F6F6] rb:border rb:border-[#F6F6F6]", {
                  'rb:border-[#FF5D34]': file.status === 'error'
                })}>
                  <img src={file.url} alt={file.name} className="rb:size-12! rb:rounded-lg rb:object-cover" />
                  {onDelete && <div
                    className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')] rb:hover:bg-[url('@/assets/images/conversation/delete_hover.svg')]"
                    onClick={() => onDelete(file)}
                  ></div>}
                </div>
              </Spin>
            )
          }
          return (
            <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
              <Flex
                align="center"
                gap={10}
                className={clsx("rb:w-45 rb:text-[12px] rb:group rb:relative rb:rounded-lg rb:bg-[#F6F6F6] rb:py-2! rb:px-2.5! rb:border rb:border-[#F6F6F6]", {
                  'rb:border-[#FF5D34]': file.status === 'error',
                  'rb:w-52': file.status === 'done' && (file.type?.includes('video') || file.type?.includes('audio'))
                })}>
                <div
                  className={clsx(
                    "rb:size-5 rb:cursor-pointer rb:bg-cover",
                    getFileIconClassName(file),
                  )}
                ></div>
                <div className="rb:flex-1 rb:w-32.5">
                  <div className="rb:leading-4 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{file.name}</div>
                  <div className="rb:leading-3.5 rb:mt-0.5 rb:text-[#5B6167] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{[file.type?.split('/').pop(), file.size].filter(item => item).join(' · ')}</div>
                </div>
                {file.status === 'done' && (file.type?.includes('video') || file.type?.includes('audio')) &&
                  <div
                    className={clsx('rb:size-4 rb:cursor-pointer rb:bg-cover', playingUid === file.uid
                      ? "rb:bg-[url('@/assets/images/file/pause.svg')]"
                      : "rb:bg-[url('@/assets/images/userMemory/play.svg')]"
                    )}
                    onClick={() => playingUid === file.uid ? handleClose() : setPlayingUid(file.uid)}
                  ></div>
                }
                {onDelete && <div
                  className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')]"
                  onClick={() => onDelete(file)}
                ></div>}
              </Flex>
            </Spin>
          )
        })}
      </Flex>

      {playingFile && (
        <div
          className="rb:fixed rb:inset-0 rb:z-1000 rb:bg-black/80 rb:flex rb:items-center rb:justify-center"
          onClick={handleClose}
        >
          <button className="ant-image-preview-close"><CloseOutlined /></button>
          {playingFile.type?.includes('video') ? (
            <video
              ref={mediaRef as React.RefObject<HTMLVideoElement>}
              src={playingFile.url}
              controls
              autoPlay
              className="rb:max-w-[90vw] rb:max-h-[90vh] rb:rounded-xl"
              onClick={e => e.stopPropagation()}
            />
          ) : (
            <audio
              ref={mediaRef as React.RefObject<HTMLAudioElement>}
              src={playingFile.url}
              controls
              autoPlay
              onClick={e => e.stopPropagation()}
            />
          )}
        </div>
      )}
    </>
  )
}

export default FileList
