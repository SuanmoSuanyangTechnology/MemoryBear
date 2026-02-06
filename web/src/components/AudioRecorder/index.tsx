import { type FC, useRef, useState } from 'react'
import RecordRTC from 'recordrtc'

import { fileUpload } from '@/api/fileStorage'

interface AudioRecorderProps {
  onRecordingComplete?: (file: { file_id: string; file_key: string; }, blob: Blob) => void
  className?: string
}

const AudioRecorder: FC<AudioRecorderProps> = ({
  onRecordingComplete,
  className = '',
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
        const formData = new FormData()
        formData.append('file', blob, `recording_${Date.now()}.webm`)
        fileUpload(formData)
          .then(res => {
            onRecordingComplete?.(res as { file_id: string; file_key: string; }, blob)
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
