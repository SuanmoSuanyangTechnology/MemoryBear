import { type FC } from 'react'
import { Flex, Spin } from 'antd'
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
  if (!fileList.length) return null

  return (
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
        if (file.type?.includes('video')) {
          return (
            <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
              <div className={clsx("rb:w-45 rb:h-12 rb:inline-block rb:group rb:relative rb:rounded-lg rb:border rb:border-[#F6F6F6]", {
                'rb:border-[#FF5D34]': file.status === 'error'
              })}>
                <video src={file.url} controls className="rb:w-45 rb:h-12 rb:rounded-lg rb:object-cover" />
                {onDelete && <div
                  className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')]"
                  onClick={() => onDelete(file)}
                ></div>}
              </div>
            </Spin>
          )
        }
        if (file.type?.includes('audio')) {
          return (
            <Spin key={`${file.url || file.uid}_${file.status}`} spinning={file.status === 'uploading'}>
              <div className={clsx("rb:w-45 rb:h-12rb:inline-flex rb:items-center rb:group rb:relative rb:rounded-lg rb:bg-[#F6F6F6] rb:py-2 rb:px-2.5 rb:gap-2 rb:border rb:border-[#F6F6F6]", {
                'rb:border-[#FF5D34]': file.status === 'error'
              })}>
                <audio src={file.url} controls className="rb:w-45 rb:h-12" />
                {onDelete && <div
                  className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')]"
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
                'rb:border-[#FF5D34]': file.status === 'error'
              })}>
              <div
                className={clsx(
                  "rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/pdf_disabled.svg')]",
                  file.type?.includes('pdf')
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
                    : file.type?.includes('text')
                    ? "rb:bg-[url('@/assets/images/file/txt.svg')]"
                    : file.type?.includes('markdown')
                    ? "rb:bg-[url('@/assets/images/file/md.svg')]"
                    : (file.type?.includes('doc') || file.type?.includes('docx') || file.type?.includes('word') || file.type?.includes('wordprocessingml.document'))
                    ? "rb:bg-[url('@/assets/images/file/word.svg')]"
                    : null
                )}
              ></div>
              <div className="rb:flex-1 rb:w-32.5">
                <div className="rb:leading-4 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{file.name}</div>
                <div className="rb:leading-3.5 rb:mt-0.5 rb:text-[#5B6167] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{[file.type?.split('/').pop(), file.size].filter(item => item).join(' · ')}</div>
              </div>
              {onDelete && <div
                className="rb:hidden rb:group-hover:block rb:absolute rb:-right-1 rb:-top-1 rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/delete.svg')]"
                onClick={() => onDelete(file)}
              ></div>}
            </Flex>
          </Spin>
        )
      })}
    </Flex>
  )
}

export default FileList
