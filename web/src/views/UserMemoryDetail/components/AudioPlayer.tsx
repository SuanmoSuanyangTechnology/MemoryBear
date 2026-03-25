/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-16 15:00:07 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 12:09:39
 */
import { type FC, useRef, useState, useEffect } from 'react'
import { Flex, Dropdown, type MenuProps, Slider } from 'antd'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'

/** Available playback speed options. */
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]

/** Format seconds into "MM:SS" display string. */
const fmt = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`

/**
 * Props for the AudioPlayer component.
 * @property src - Audio file URL to play.
 * @property fileName - Display name shown beside the file icon.
 * @property fileSize - Human-readable file size string (e.g. "3.2 MB").
 */
interface AudioPlayerProps {
  src: string
  fileName: string
  fileSize: string
}

/**
 * AudioPlayer – A compact inline audio player with playback controls.
 *
 * Displays file metadata (name & size), a play/pause toggle, a seekable
 * progress slider, elapsed/total time, and a dropdown menu for downloading
 * the file or changing playback speed.
 *
 * @example
 * <AudioPlayer src="/audio/demo.mp3" fileName="demo.mp3" fileSize="3.2 MB" />
 */
const AudioPlayer: FC<AudioPlayerProps> = ({ src, fileName, fileSize }) => {
  const { t } = useTranslation()
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [duration, setDuration] = useState(0)
  const [speed, setSpeed] = useState(1)

  /* Bind native audio events to sync React state; re-binds when src changes. */
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const onTime = () => setCurrent(audio.currentTime)
    const onMeta = () => setDuration(audio.duration)
    const onEnd = () => setPlaying(false)
    audio.addEventListener('timeupdate', onTime)
    audio.addEventListener('loadedmetadata', onMeta)
    audio.addEventListener('ended', onEnd)
    return () => {
      audio.removeEventListener('timeupdate', onTime)
      audio.removeEventListener('loadedmetadata', onMeta)
      audio.removeEventListener('ended', onEnd)
    }
  }, [src])

  /** Toggle between play and pause. */
  const togglePlay = () => {
    const audio = audioRef.current
    if (!audio) return
    if (playing) { audio.pause(); setPlaying(false) }
    else { audio.play(); setPlaying(true) }
  }

  /** Seek to a specific position (in seconds) on the audio timeline. */
  const handleSeek = (val: number) => {
    if (audioRef.current) audioRef.current.currentTime = val
    setCurrent(val)
  }

  /** Update playback speed on both React state and the native audio element. */
  const setPlaybackSpeed = (s: number) => {
    setSpeed(s)
    if (audioRef.current) audioRef.current.playbackRate = s
  }

  /** Open the audio source URL in a new tab to trigger download. */
  const handleDownload = () => window.open(src, '_blank')

  /** Dropdown menu items: download and playback speed sub-menu. */
  const mainMenu: MenuProps = {
    items: [
      {
        key: 'download',
        icon: <div className="rb:size-6 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/userMemory/download.svg')]" />,
        label: t('common.download'),
        onClick: handleDownload,
      },
      {
        key: 'speed',
        icon: <div className="rb:size-6 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/userMemory/play_speed.svg')]" />,
        label: t('perceptualDetail.playbackSpeed'),
        children: SPEEDS.map(s => ({
          key: String(s),
          label: <span className={s === speed ? 'rb:font-bold rb:text-[#171719]' : ''}>{s === 1 ? 'normal' : s}</span>,
          onClick: () => setPlaybackSpeed(s),
        })),
      },
    ],
  }

  return (
    <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3 rb:w-full">
      <audio ref={audioRef} src={src} preload="metadata" />
      <Flex align="center" justify="space-between" className="rb:mb-2">
        <Flex align="center" gap={12}>
          <div className="rb:w-7.5 rb:h-9 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/mp3.svg')]" />
          <div className="rb:flex-1">
            <div className="rb:font-medium rb:leading-5 rb:text-[14px] rb:wrap-break-word rb:line-clamp-1">{fileName}</div>
            <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">{fileSize || '-'}</div>
          </div>
        </Flex>
        <Flex align="center" gap={12}>
          <div
            className={clsx("rb:cursor-pointer rb:size-6", {
              "rb:bg-[url('@/assets/images/userMemory/play.svg')]": !playing,
              "rb:bg-[url('@/assets/images/userMemory/pause.svg')]": playing,
            })}
            onClick={togglePlay}
          ></div>
          
          <Dropdown menu={mainMenu} trigger={['click']} placement="bottomRight">
            <div className="rb:cursor-pointer rb:size-6 rb:bg-[url('@/assets/images/common/more.svg')] rb:hover:bg-[url('@/assets/images/common/more_hover.svg')]"></div>
          </Dropdown>
        </Flex>
      </Flex>
      <Flex align="center" gap={8} className="rb:mt-3!">
        <Slider
          min={0}
          max={duration || 0}
          step={0.1}
          value={current}
          onChange={handleSeek}
          tooltip={{ formatter: null }}
          className="rb:flex-1 rb:m-0!"
          styles={{ track: { background: '#171719' }, rail: { background: '#E4E4E4' }, handle: { display: 'none' } }}
        />
        <span className="rb:text-[12px] rb:leading-4.5 rb:text-[#5B6167] rb:whitespace-nowrap">{fmt(current)} / {fmt(duration)}</span>
      </Flex>
    </div>
  )
}

export default AudioPlayer
