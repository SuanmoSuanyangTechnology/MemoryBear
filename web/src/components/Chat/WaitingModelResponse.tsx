import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

const HIGHLIGHT_INTERVAL = 250

const WaitingModelResponse = () => {
  const { t } = useTranslation()
  const text = t('memoryConversation.waitingModelResponse')
  const characters = Array.from(text)
  const [highlightIndex, setHighlightIndex] = useState(0)

  useEffect(() => {
    if (characters.length <= 1) return

    const intervalId = window.setInterval(() => {
      setHighlightIndex(currentIndex => (currentIndex + 1) % characters.length)
    }, HIGHLIGHT_INTERVAL)

    return () => window.clearInterval(intervalId)
  }, [characters.length])

  const activeIndex = characters.length > 0 ? highlightIndex % characters.length : -1

  return (
    <span className="rb:inline-block rb:text-[12px]" aria-label={text}>
      {characters.map((character, index) => (
        <span
          key={`${character}-${index}`}
          aria-hidden="true"
          className={index === activeIndex
            ? 'rb:text-gray-900 rb:transition-colors rb:duration-200 rb:ease-in-out rb:motion-reduce:text-gray-500 rb:motion-reduce:transition-none'
            : 'rb:text-gray-500 rb:transition-colors rb:duration-200 rb:ease-in-out rb:motion-reduce:transition-none'}
        >
          {character}
        </span>
      ))}
    </span>
  )
}

export default WaitingModelResponse
