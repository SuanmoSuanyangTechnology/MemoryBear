import { useRef, useState, useLayoutEffect, useCallback, type ReactNode } from 'react'
import { Popover, type PopoverProps } from 'antd'
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
    let total = widths.reduce((sum, w, i) => sum + (i > 0 ? gap : 0) + w, 0)
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
  }, [items, gap])

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
    <div ref={containerRef} style={{ width: '100%', minWidth: 0 }}>
      {/* off-screen measure layer */}
      <div ref={measureRef} style={{ display: 'flex', gap, position: 'fixed', top: -9999, left: -9999, visibility: 'hidden', pointerEvents: 'none' }}>
        {items.map((item, i) => <span key={i}>{item}</span>)}
        <Tag>+0</Tag>
      </div>
      <Popover
        content={
          <div style={{ display: 'flex', gap, flexWrap: 'wrap', maxWidth: 300 }}>
            {items.map((item, i) => <span key={i}>{item}</span>)}
          </div>
        }
        {...(popoverProps || {})}
        open={popoverProps === false ? false : undefined}
      >
        <div style={{ display: 'flex', gap, alignItems: 'center', flexWrap: 'nowrap' }}>
          {items.slice(0, visibleCount).map((item, i) => <span key={i}>{item}</span>)}
          {hidden > 0 && numTag
            ? numTag(hidden)
            : hidden > 0 && <Tag color={numTagColor}>+{hidden}</Tag>
          }
        </div>
      </Popover>
    </div>
  )
}

export default OverflowTags
