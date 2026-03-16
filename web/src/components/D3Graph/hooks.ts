import { useRef, useEffect } from 'react'
import * as d3 from 'd3'

/**
 * Generic hook that mounts a D3 graph inside a div container.
 * Clears any existing SVG before calling initFn, and runs cleanup on unmount or dep change.
 */
export function useD3Graph<T>(
  initFn: (container: HTMLDivElement) => (() => void) | void,
  deps: T[]
) {
  const containerRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    d3.select(container).selectAll('svg').remove()
    const cleanup = initFn(container)
    return () => {
      cleanup?.()
      d3.select(container).selectAll('svg').remove()
    }
  }, deps)
  return containerRef
}
