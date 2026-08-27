import { useRef, useState, useLayoutEffect, useCallback, type ReactNode } from 'react'
import { Popover, Flex, type PopoverProps } from 'antd'
import Tag, { type TagProps } from '@/components/Tag'

interface OverflowTagsProps {
  items?: ReactNode[];
  gap?: number;
  numTagColor?: TagProps['color'];
  numTag?: (num?: number) => ReactNode;
  popoverProps?: PopoverProps | false;
}

const OverflowTags = ({ items = [], gap = 8, numTagColor = 'default', numTag, popoverProps }: OverflowTagsProps) => {
  const containerRef = useRef<HTMLDivElement>(null)
  const measureRef = useRef<HTMLDivElement>(null)
  const [visibleCount, setVisibleCount] = useState(items.length)

  const calculate = useCallback((containerWidth: number) => {
    const measure = measureRef.current
    if (!measure || containerWidth === 0) return

    const children = Array.from(measure.children) as HTMLElement[]
    if (!children.length) { setVisibleCount(0); return }

    // last child is the sample +N tag
    const extraTagWidth = (children[children.length - 1] as HTMLElement).offsetWidth
    const widths = children.slice(0, -1).map(c => c.offsetWidth)

    // check if all items fit
    const total = widths.reduce((sum, w, i) => sum + (i > 0 ? gap : 0) + w, 0)
    if (total <= containerWidth) {
      setVisibleCount(widths.length)
      return
    }

    // find max count that fits alongside +N
    let used = 0
    let count = 0
    for (let i = 0; i < widths.length; i++) {
      const w = used + (i > 0 ? gap : 0) + widths[i]
      if (w + gap + extraTagWidth <= containerWidth) {
        used = w
        count = i + 1
      } else {
        break
      }
    }
    setVisibleCount(count || 1)
  }, [gap])

  useLayoutEffect(() => {
    const ro = new ResizeObserver(entries => {
      calculate(entries[0].contentRect.width)
    })
    if (containerRef.current) {
      ro.observe(containerRef.current)
    }
    return () => ro.disconnect()
  }, [calculate])

  const hidden = items.length - visibleCount

  return (
    <div ref={containerRef} className="rb:w-full rb:min-w-0 rb:overflow-hidden">
      {/* off-screen measure layer */}
      <Flex
        ref={measureRef}
        gap={gap}
        wrap={false}
        className="rb:fixed rb:-top-9999 rb:-left-9999 rb:invisible rb:w-max rb:pointer-events-none"
      >
        {items.map((item, i) => (
          <span key={i} className="rb:shrink-0 rb:whitespace-nowrap">{item}</span>
        ))}
        <span className="rb:shrink-0 rb:whitespace-nowrap">
          {numTag
            ? numTag(items.length)
            : <Tag color={numTagColor}>+{items.length}</Tag>
          }
        </span>
      </Flex>
      <Popover
        content={
          <Flex gap={gap} wrap className="rb:max-w-75 rb:max-h-50 rb:overflow-y-auto">
            {items.map((item, i) => <span key={i}>{item}</span>)}
          </Flex>
        }
        placement="topLeft"
        {...(popoverProps || {})}
        open={popoverProps === false ? false : undefined}
      >
        <Flex gap={gap} align="center" wrap={false}>
          {items.slice(0, visibleCount).map((item, i) => (
            <span key={i} className="rb:shrink-0 rb:whitespace-nowrap">{item}</span>
          ))}
          {hidden > 0 && (
            <span className="rb:shrink-0 rb:whitespace-nowrap">
              {numTag
                ? numTag(hidden)
                : <Tag color={numTagColor}>+{hidden}</Tag>
              }
            </span>
          )}
        </Flex>
      </Popover>
    </div>
  )
}

export default OverflowTags
