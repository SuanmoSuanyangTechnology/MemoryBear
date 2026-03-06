import { type FC, useRef, useState } from 'react'
import RecordRTC from 'recordrtc'

import { fileUploadUrlWithoutApiPrefix } from '@/api/fileStorage'
import { request } from '@/utils/request'

interface AudioRecorderProps {
  onRecordingComplete?: (file: { file_id: string; file_key: string; url: string; type?: string; }, blob?: Blob) => void
  className?: string;
  action?: string;
  requestConfig?: Record<string, any>;
}

const AudioRecorder: FC<AudioRecorderProps> = ({
  onRecordingComplete,
  className = '',
  action = fileUploadUrlWithoutApiPrefix,
  requestConfig = {}
}) => {
  const [isRecording, setIsRecording] = useState(false)
  const recorderRef = useRef<RecordRTC | null>(null)

  const startRecording = async () => {
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

  const stopRecording = () => {
    if (recorderRef.current) {
      recorderRef.current.stopRecording(() => {
        const blob = recorderRef.current!.getBlob()
        const url = recorderRef.current!.toURL()
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
            recorderRef.current?.destroy()
            recorderRef.current = null
          })
      })
      setIsRecording(false)
    }
  }

  return (
    <div
      className={`rb:size-5.5 rb:cursor-pointer rb:bg-cover ${className} ${
        isRecording
          ? `rb:bg-[url('@/assets/images/conversation/audio_ing.gif')]`
          : `rb:bg-[url('@/assets/images/conversation/audio.svg')] rb:hover:bg-[url('@/assets/images/conversation/audio_hover.svg')]`
      }`}
      onClick={isRecording ? stopRecording : startRecording}
    />
  )
}

export default AudioRecorder
