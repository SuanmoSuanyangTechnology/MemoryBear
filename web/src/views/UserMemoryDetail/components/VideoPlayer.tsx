/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-24 12:21:56 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-24 12:21:56 
 */
import { type FC, useRef, useState } from 'react'
import { CloseOutlined } from '@ant-design/icons'
interface VideoPlayerProps {
  src: string
}

const VideoPlayer: FC<VideoPlayerProps> = ({ src }) => {
  const [open, setOpen] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)

  const handleOpen = () => setOpen(true)

  const handleClose = () => {
    videoRef.current?.pause()
    setOpen(false)
  }

  return (
    <>
      {/* Thumbnail with play overlay */}
      <div
        className="rb:relative rb:w-full rb:h-45 rb:rounded-xl rb:overflow-hidden rb:cursor-pointer rb:group"
        onClick={handleOpen}
      >
        <video src={src} className="rb:w-full rb:h-full rb:object-cover" preload="metadata" />
        <div className="rb:absolute rb:inset-0 rb:bg-black/20 rb:flex rb:items-center rb:justify-center rb:transition-colors group-hover:rb:bg-black/30">
          <div className="rb:size-10 rb:rounded-full rb:bg-white/80 rb:flex rb:items-center rb:justify-center">
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M5 3.5L14.5 9L5 14.5V3.5Z" fill="#171719" />
            </svg>
          </div>
        </div>
      </div>

      {/* Fullscreen modal */}
      {open && (
        <div
          className="rb:fixed rb:inset-0 rb:z-1000 rb:bg-black/80 rb:flex rb:items-center rb:justify-center"
          onClick={handleClose}
        >
          <button className="ant-image-preview-close"><CloseOutlined /></button>
          <video
            ref={videoRef}
            src={src}
            controls
            autoPlay
            className="rb:max-w-[90vw] rb:max-h-[90vh] rb:rounded-xl"
            onClick={e => e.stopPropagation()}
          />
        </div>
      )}
    </>
  )
}

export default VideoPlayer
