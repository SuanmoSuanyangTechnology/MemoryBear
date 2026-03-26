/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:11:51 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 14:25:26
 */
import { type FC, useRef, useState } from 'react'
import RecordRTC from 'recordrtc'
import { App, Tooltip } from 'antd'
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { fileUploadUrlWithoutApiPrefix } from '@/api/fileStorage'
import { request } from '@/utils/request'

/** Props for the AudioRecorder component */
interface AudioRecorderProps {
  /** Callback fired when recording is complete, receives uploaded file info and raw blob */
  onRecordingComplete?: (file: { file_id: string; file_key: string; url: string; type?: string; }, blob?: Blob) => void
  className?: string;
  /** Upload endpoint URL, defaults to fileUploadUrlWithoutApiPrefix */
  action?: string;
  /** Additional config passed to the upload request */
  requestConfig?: Record<string, any>;
  disabled?: boolean;
  maxSize?: number;
}

const AudioRecorder: FC<AudioRecorderProps> = ({
  onRecordingComplete,
  className = '',
  action = fileUploadUrlWithoutApiPrefix,
  requestConfig = {},
  disabled = false,
  maxSize,
}) => {
  const { message } = App.useApp()
  const { t } = useTranslation();
  // Whether the recorder is currently capturing audio
  const [isRecording, setIsRecording] = useState(false)
  // Holds the RecordRTC instance across renders
  const recorderRef = useRef<RecordRTC | null>(null)

  /** Request microphone access and start recording */
  const startRecording = async () => {
    if (disabled) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      recorderRef.current = new RecordRTC(stream, {
        type: 'audio',
        mimeType: 'audio/webm'
      })
      recorderRef.current.startRecording()
      setIsRecording(true)
    } catch (error) {
      console.error('Failed to start recording:', error)
    }
  }

  /** Stop recording, upload the audio blob, then invoke the completion callback */
  const stopRecording = () => {
    if (disabled) return
    if (recorderRef.current) {
      recorderRef.current.stopRecording(() => {
        const blob = recorderRef.current!.getBlob()
        const url = recorderRef.current!.toURL()

        if (maxSize && blob.size > maxSize * 1024 * 1024) {
          message.error(t('common.fileSizeTip', { size: maxSize }));
          return
        }

        const formData = new FormData()
        formData.append('file', blob, `recording_${Date.now()}.webm`)
        request
          .uploadFile(action, formData, requestConfig)
          .then(res => {
            onRecordingComplete?.({
              ...(res as { file_id: string; file_key: string }),
              type: blob.type,
              url
            }, blob)
            // Release recorder resources after upload
            recorderRef.current?.destroy()
            recorderRef.current = null
          })
      })
      setIsRecording(false)
    }
  }

  // Toggle between recording/idle states on click;
  // swap background image to reflect current state
  return (
    <Tooltip title={isRecording ? t('memoryConversation.stopAudioRecorder') : t('memoryConversation.startAudioRecorder')}>
      <div
        className={clsx("rb:bg-cover", className, {
          'rb:cursor-pointer': !disabled,
          'rb:opacity-65 rb:cursor-not-allowed': disabled,
          "rb:size-4 rb:bg-[url('@/assets/images/conversation/audio.svg')]": !isRecording,
          "rb:size-6 rb:bg-[url('@/assets/images/conversation/audio_ing.gif')]": isRecording,
        })}
        onClick={isRecording ? stopRecording : startRecording}
      />
    </Tooltip>
  )
}

export default AudioRecorder
