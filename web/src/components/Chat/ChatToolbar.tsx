/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-17 14:22:25 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 15:44:48
 */
// Toolbar component for chat input area, supporting file upload, audio recording, and variable configuration
import { useRef, forwardRef, useImperativeHandle, type ReactNode, useEffect } from 'react'
import { Flex, Dropdown, Divider, App, Form, type MenuProps, Tooltip } from 'antd'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'

import AudioRecorder from '@/components/AudioRecorder'
import UploadFiles from '@/views/Conversation/components/FileUpload'
import UploadFileListModal from '@/views/Conversation/components/UploadFileListModal'
import VariableConfigModal from '@/views/Workflow/components/Chat/VariableConfigModal'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'
import type { UploadFileListModalRef } from '@/views/Conversation/types'
import type { VariableConfigModalRef } from '@/views/Workflow/types'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'

// Exposed methods via ref for parent components to access/set form state
export interface ChatToolbarRef {
  getFiles: () => any[]
  getVariables: () => Variable[]
  setFiles: (files: any[]) => void
  setVariables: (variables: Variable[]) => void
}

// Props for configuring toolbar features, upload settings, and event callbacks
export interface ChatToolbarProps {
  features: FeaturesConfigForm
  leftExtra?: ReactNode;
  rightExtra?: ReactNode
  uploadAction?: string
  uploadRequestConfig?: {
    data?: Record<string, string | number | boolean>
    headers?: Record<string, string>
  }
  onFilesChange?: (files: any[]) => void
  onVariablesChange?: (variables: Variable[]) => void
  onRecordingComplete?: (file: any) => void;
  defaultValue?: { memory: boolean }
}

interface FormValues {
  files: any[]
  variables: Variable[];
  memory?: boolean;
}

const max_file_count = 1;
const ChatToolbar = forwardRef<ChatToolbarRef, ChatToolbarProps>(({
  features,
  leftExtra,
  rightExtra,
  uploadAction,
  uploadRequestConfig,
  onFilesChange,
  onVariablesChange,
  onRecordingComplete,
  defaultValue,
}, ref) => {
  const { t } = useTranslation()
  const { message: messageApi } = App.useApp()
  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null)
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)
  const [form] = Form.useForm<FormValues>()
  const queryValues = Form.useWatch([], form)

  useEffect(() => {
    if (!defaultValue) return
    form.setFieldsValue(defaultValue)
  }, [defaultValue])

  useImperativeHandle(ref, () => ({
    getFiles: () => form.getFieldValue('files') || [],
    getVariables: () => form.getFieldValue('variables') || [],
    setFiles: (files) => form.setFieldValue('files', files),
    setVariables: (variables) => {
      console.log('variables', variables)
      form.setFieldValue('variables', variables)
    },
  }))

  const { file_upload } = features || {}

  // Append newly uploaded file to the file list when upload is complete
  const fileChange = (file?: any) => {
    console.log('file', file)
    const lastFiles = form.getFieldValue('files') || [];
    const index = lastFiles.findIndex((item: any) => item.uid === file.uid)
    if (index > -1) {
      lastFiles[index] = file
    } else {
      lastFiles.push(file)
    }
    form.setFieldValue('files', [...lastFiles])
    onFilesChange?.([...lastFiles])
  }

  // Append recorded audio file to the file list and notify parent
  const handleRecordingComplete = (file: any) => {
    const files = [...(queryValues?.files || []), file]
    form.setFieldValue('files', files)
    onFilesChange?.(files)
    onRecordingComplete?.(file)
  }

  // Merge a batch of files (e.g. from remote URL modal) into the file list
  const addFileList = (list?: any[]) => {
    if (!list?.length) return
    const files = [...(queryValues?.files || []), ...list]
    form.setFieldValue('files', files)
    onFilesChange?.(files)
  }

  // Persist variable values from the config modal and notify parent
  const handleVariablesSave = (values: Variable[]) => {
    form.setFieldValue('variables', values)
    onVariablesChange?.(values)
  }

  // True when any required variable is missing a value, used to highlight the config button
  const isNeedVariableConfig = queryValues?.variables?.some(
    vo => vo.required && (vo.value === null || vo.value === undefined || vo.value === '')
  )

  // Build dropdown menu items based on allowed transfer methods
  const fileMenus: MenuProps['items'] = []
  const enabledTypes = ['image', 'document', 'video', 'audio'].filter(
    type => file_upload?.[`${type}_enabled` as keyof FeaturesConfigForm['file_upload']]
  )
  if (file_upload?.allowed_transfer_methods?.includes('remote_url') && enabledTypes.length > 0) {
    fileMenus.push({
      key: 'url',
      label: t('memoryConversation.addRemoteFile'),
      onClick: () => {
        if ((queryValues?.files?.length || 0) >= max_file_count) {
          messageApi.warning(t('common.fileNumTip', { num: max_file_count }))
          return
        }
        uploadFileListModalRef.current?.handleOpen()
      }
    })
  }
  if (file_upload?.allowed_transfer_methods?.includes('local_file') && enabledTypes.length > 0) {
    fileMenus.push({
      key: 'upload',
      label: (
        <UploadFiles
          action={uploadAction}
          onChange={fileChange}
          requestConfig={uploadRequestConfig}
          featureConfig={file_upload}
          disabled={(queryValues?.files?.length || 0) >= max_file_count}
        />
      )
    })
  }

  return (
    <Form form={form} initialValues={{ files: [], variables: [] }}>
      <Flex justify="space-between" className="rb:flex-1">
        <Flex gap={8} align="center" justify="start">
          <Form.Item name="files" noStyle hidden={!file_upload?.enabled || fileMenus.length === 0}>
            <Dropdown menu={{ items: fileMenus }}>
              <Flex justify="center" align="center" className="rb:size-7 rb-border rb:cursor-pointer rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]">
                <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/link.svg')]" />
              </Flex>
            </Dropdown>
          </Form.Item>

          {leftExtra}
          <Form.Item name="variables" className="rb:mb-0!" hidden={queryValues?.variables?.length < 1}>
            <Tooltip title={t('memoryConversation.variableConfig')}>
              <Flex justify="center" align="center"
                className={clsx("rb:size-7 rb:border rb:cursor-pointer rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
                  'rb:border-[#FF5D34]': isNeedVariableConfig,
                  'rb:border-[#EBEBEB]': !isNeedVariableConfig,
                })}
                onClick={() => variableConfigModalRef.current?.handleOpen(queryValues.variables)}
              >
                <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/variables.svg')]" />
              </Flex>
            </Tooltip>
          </Form.Item>
        </Flex>
        
        <Flex align="center" justify="end" gap={8}>
          {rightExtra}
          {file_upload?.audio_enabled && file_upload?.allowed_transfer_methods?.includes('local_file')  &&
            <AudioRecorder
              disabled={(queryValues?.files?.length || 0) >= max_file_count}
              action={uploadAction}
              requestConfig={uploadRequestConfig}
              onRecordingComplete={handleRecordingComplete}
              maxSize={file_upload?.audio_max_size_mb}
            />
          }
          {(rightExtra || (file_upload?.audio_enabled && file_upload?.allowed_transfer_methods?.includes('local_file'))) && <Divider type="vertical" className="rb:ml-1.5! rb:mr-0! rb:h-4!" />}
        </Flex>
      </Flex>

      <UploadFileListModal
        ref={uploadFileListModalRef}
        refresh={addFileList}
        featureConfig={file_upload}
      />
      <VariableConfigModal
        ref={variableConfigModalRef}
        refresh={handleVariablesSave}
      />
    </Form>
  )
})

export default ChatToolbar
