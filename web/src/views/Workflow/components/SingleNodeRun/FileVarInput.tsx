/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-07 18:37:23 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-19 15:16:29
 */
import { type FC, useState, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Form, Row, Col } from 'antd'
import type { FormInstance } from 'antd'
import UploadFiles, { transform_file_type } from '@/views/Conversation/components/FileUpload'
import UploadFileListModal from '@/views/Conversation/components/UploadFileListModal'
import type { UploadFileListModalRef } from '@/views/Conversation/types'
import FileList from '@/components/Chat/FileList'
import { getFileInfoByUrl } from '@/api/fileStorage'

interface FileVarInputProps {
  name: string | string[];
  dataType: string;
  form: FormInstance;
  defaultValue?: any[];
}

const FileVarInput: FC<FileVarInputProps> = ({ name, form, defaultValue = [] }) => {
  const { t } = useTranslation()
  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null)
  const [fileList, setFileList] = useState<any[]>(Array.isArray(defaultValue) ? defaultValue : defaultValue ? [defaultValue] : [])

  const setFormFileValue = (updated: any[]) => {
    form.setFieldValue(name, updated)
  }

  const fileChange = (file?: any) => {
    const fileObj = file ? {
      ...file,
      type: file.type,
      transfer_method: 'local_file',
      upload_file_id: file.response?.data?.file_id,
    } : undefined
    setFileList(prev => {
      const index = prev.findIndex((item: any) => item.uid === fileObj.uid)
      const updated = index > -1
        ? prev.map((item, i) => i === index ? fileObj : item)
        : [...prev, fileObj]
      setTimeout(() => setFormFileValue(updated), 0)
      return updated
    })
  }

  const addFileList = (list?: any[]) => {
    if (!list?.length) return
    const uploadingList = list.map(f => ({ ...f, status: 'uploading' }))
    setFileList(prev => {
      const updated = [...prev, ...uploadingList]
      setTimeout(() => setFormFileValue(updated), 0)
      return updated
    });
    uploadingList.forEach(file => {
      getFileInfoByUrl(file.url)
        .then((res) => {
          const { file_name, file_size, content_type } = res as { file_name: string; file_size: number; content_type: string }
          setFileList(prev => {
            const updated = prev.map(f =>
              f.uid === file.uid
                ? { ...f, status: 'done', name: file_name, size: file_size, type: transform_file_type[content_type] || content_type }
                : f
            )
            setFormFileValue(updated)
            return updated
          })
        })
        .catch(() => {
          setFileList(prev => {
            const updated = prev.map(f => f.uid === file.uid ? { ...f, status: 'error' } : f)
            setFormFileValue(updated)
            return updated
          })
        })
    })
  }

  const previewFileList = useMemo(() => fileList.map(file => ({
    ...file,
    url: file.thumbUrl || file.url || (file.originFileObj ? URL.createObjectURL(file.originFileObj) : undefined)
  })), [fileList])

  const handleDelete = (file: any) => {
    const updated = fileList.filter(item =>
      item.thumbUrl && file.thumbUrl ? item.thumbUrl !== file.thumbUrl
        : item.url && file.url ? item.url !== file.url
        : item.uid !== file.uid
    )
    setFileList(updated)
    setFormFileValue(updated)
  }

  return (
    <>
      <UploadFileListModal ref={uploadFileListModalRef} refresh={addFileList} />
      <Form.Item name={name} hidden noStyle />
      <Form.Item>
        <Row gutter={8}>
          <Col span={12}>
            <UploadFiles
              onChange={fileChange}
              block={true}
              textType="button"
              disabled={fileList.length > 0}
            />
          </Col>
          <Col span={12}>
            <Button block
              disabled={fileList.length > 0}
              onClick={() => uploadFileListModalRef.current?.handleOpen()}>
              {t('memoryConversation.addRemoteFile')}
            </Button>
          </Col>
        </Row>
        {previewFileList.length > 0 && (
          <FileList wrap="wrap" fileList={previewFileList} onDelete={handleDelete} className="rb:mt-2!" />
        )}
      </Form.Item>
    </>
  )
}

export default FileVarInput
