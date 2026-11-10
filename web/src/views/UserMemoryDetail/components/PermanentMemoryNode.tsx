import type { PermanentMemoryFact } from './PermanentMemoryDetailModal'

interface PermanentMemoryNodeProps {
  fact: PermanentMemoryFact;
  onOpen: (fact: PermanentMemoryFact) => void;
}

const generalColor = 'rb:bg-orange-100 rb:border-orange-200/20 rb:text-yellow-900'

const fieldColors = [
  'rb:bg-green-100 rb:border-green-800/10 rb:text-green-800',
  'rb:bg-purple-100/40 rb:border-purple-300/10 rb:text-purple-900',
  'rb:bg-blue-50 rb:border-blue-100 rb:text-blue-300',
  'rb:bg-pink-50 rb:border-pink-100 rb:text-pink-300',
  'rb:bg-cyan-50 rb:border-cyan-100 rb:text-cyan-300',
  'rb:bg-red-50 rb:border-red-100 rb:text-red-400',
]

/** Renders a clickable capsule positioned by the panel physics simulation. */
export default function PermanentMemoryNode({ fact, onOpen }: PermanentMemoryNodeProps) {
  const color = fact.colorIndex === undefined
    ? generalColor
    : fieldColors[fact.colorIndex % fieldColors.length]

  return (
    <button
      data-profile-tag={fact.id}
      type="button"
      onClick={() => onOpen(fact)}
      className={`rb:absolute rb:left-0 rb:top-0 rb:max-w-[85%] rb:shrink-0 rb:cursor-pointer rb:rounded-2xl rb:border rb:border-solid rb:px-3 rb:py-2.5 rb:text-center rb:text-xs rb:font-medium rb:leading-3.5 rb:wrap-break-word rb:shadow-xs rb:transition-shadow rb:hover:shadow-md rb:focus-visible:outline-2 rb:focus-visible:outline-primary-600 ${color}`}
    >
      {fact.title}
    </button>
  )
}
