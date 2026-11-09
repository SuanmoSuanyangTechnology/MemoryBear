import { useLayoutEffect, useRef } from 'react'
import { Bodies, Body, Composite, Engine, Sleeping } from 'matter-js'

const step = 1000 / 60

/** Simulates the prototype's gravity-driven capsule pile within the viewport. */
export default function usePermanentMemoryDrop(contentKey: string, loading: boolean) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const viewport = viewportRef.current
    const stage = stageRef.current
    if (loading || !viewport || !stage) return
    const card = viewport.parentElement?.parentElement
    const column = card?.parentElement
    if (!card || !column) return
    const nodes = Array.from(stage.querySelectorAll<HTMLButtonElement>('[data-profile-tag]'))
    if (!nodes.length) return

    const engine = Engine.create({ enableSleeping: true, positionIterations: 10, velocityIterations: 8 })
    engine.gravity.y = 0.8
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let bodies: Body[] = []
    let ceiling: Body | undefined
    let frame = 0
    let resizeFrame = 0
    let previousTime = 0
    let accumulator = 0
    let width = 0
    let height = 0
    let disposed = false
    let contentOffset = 0
    let compacted = false

    const fitSettledContent = () => {
      if (compacted || bodies.some(body => !body.isSleeping)) return
      compacted = true
      // Remove empty horizontal bands while preserving overlapping rows and rotation.
      const ordered = [...bodies].sort((left, right) => left.bounds.min.y - right.bounds.min.y)
      let previousBottom = ordered[0].bounds.max.y
      let removedSpace = 0
      ordered.slice(1).forEach(body => {
        const gap = body.bounds.min.y - removedSpace - previousBottom
        removedSpace += Math.max(0, gap - 12)
        if (removedSpace > 0) Body.translate(body, { x: 0, y: -removedSpace })
        previousBottom = Math.max(previousBottom, body.bounds.max.y)
      })
      const top = Math.min(...bodies.map(body => body.bounds.min.y))
      const bottom = Math.max(...bodies.map(body => body.bounds.max.y))
      contentOffset = Math.max(0, top - 12)
      const contentHeight = Math.ceil(bottom - contentOffset + 12)
      const scrollTop = viewport.scrollTop
      stage.style.height = `${contentHeight}px`
      viewport.style.height = `${Math.min(height, contentHeight)}px`
      viewport.scrollTop = Math.max(0, scrollTop - contentOffset)
    }

    const render = () => {
      bodies.forEach((body, index) => {
        const node = nodes[index]
        node.style.transform = `translate(${body.position.x - node.offsetWidth / 2}px, ${body.position.y - node.offsetHeight / 2 - contentOffset}px) rotate(${body.angle}rad)`
      })
    }

    const update = () => {
      bodies.forEach(body => {
        if (Math.abs(body.angle) > 0.2) {
          Body.setAngle(body, 0.2 * Math.sign(body.angle))
          Body.setAngularVelocity(body, -0.2 * body.angularVelocity)
        }
      })
      Engine.update(engine, step)
      if (!ceiling && bodies.every(body => body.bounds.min.y >= 4)) {
        ceiling = Bodies.rectangle(width / 2, -30, width + 200, 64, { isStatic: true })
        Composite.add(engine.world, ceiling)
      }
    }

    const tick = (time: number) => {
      frame = 0
      if (disposed || document.hidden) return
      accumulator += Math.min(previousTime ? time - previousTime : step, 50)
      previousTime = time
      while (accumulator >= step) {
        update()
        accumulator -= step
      }
      fitSettledContent()
      render()
      if (bodies.some(body => !body.isSleeping)) frame = requestAnimationFrame(tick)
    }

    const resume = () => {
      if (frame || disposed || document.hidden) return
      previousTime = 0
      accumulator = 0
      frame = requestAnimationFrame(tick)
    }

    const layout = (force = false) => {
      const nextWidth = viewport.clientWidth
      const chromeHeight = card.offsetHeight - viewport.clientHeight
      const nextHeight = Math.max(1, column.clientHeight - chromeHeight)
      if (!nextWidth || !nextHeight) return
      if (!force && Math.abs(width - nextWidth) < 1 && Math.abs(height - nextHeight) < 1) return
      const animate = !motion.matches && (!width || bodies.some(body => !body.isSleeping))
      cancelAnimationFrame(frame)
      frame = 0
      Composite.clear(engine.world, false)
      Engine.clear(engine)
      ceiling = undefined
      contentOffset = 0
      compacted = false
      width = nextWidth
      height = nextHeight
      const sizes = nodes.map(node => ({ width: node.offsetWidth + 3, height: node.offsetHeight + 3 }))
      const area = sizes.reduce((total, size) => total + size.width * size.height, 0)
      const tallestNode = Math.max(...sizes.map(size => size.height + size.width * Math.sin(0.2)))
      const stageHeight = Math.ceil(Math.max(tallestNode + 24, area / Math.max(1, width - 24) * 1.8 + 64))
      const viewportHeight = Math.min(height, stageHeight)
      viewport.style.height = `${viewportHeight}px`
      stage.style.height = `${stageHeight}px`
      const viewportTop = Math.max(0, stageHeight - viewportHeight)
      let spawnBottom = viewportTop - 8
      bodies = sizes.map((size, index) => {
        const fraction = (0.61803398875 * index) % 1
        const angle = 0.065 * (index % 5 - 2)
        const rotatedHeight = size.height * Math.cos(angle) + size.width * Math.abs(Math.sin(angle))
        const rotatedWidth = size.width * Math.cos(angle) + size.height * Math.abs(Math.sin(angle))
        const x = rotatedWidth / 2 + 12 + fraction * Math.max(0, width - rotatedWidth - 24)
        const y = spawnBottom - rotatedHeight / 2
        spawnBottom -= rotatedHeight + 12
        return Bodies.rectangle(x, y, size.width, size.height, {
          chamfer: { radius: Math.min(18, size.height / 2 - 1) },
          angle,
          restitution: 0.22,
          friction: 0.65,
          frictionStatic: 0.9,
          frictionAir: 0.035,
          sleepThreshold: 45,
        })
      })
      Composite.add(engine.world, [
        ...bodies,
        Bodies.rectangle(width / 2, stageHeight + 24, width + 200, 64, { isStatic: true, friction: 0.8 }),
        Bodies.rectangle(-36, stageHeight / 2 - 600, 80, stageHeight + 2400, { isStatic: true }),
        Bodies.rectangle(width + 36, stageHeight / 2 - 600, 80, stageHeight + 2400, { isStatic: true }),
      ])
      if (!animate) {
        for (let index = 0; index < 540; index++) update()
        bodies.forEach(body => Sleeping.set(body, true))
      }
      viewport.scrollTop = viewportTop
      fitSettledContent()
      render()
      resume()
    }

    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(resizeFrame)
      resizeFrame = requestAnimationFrame(() => layout())
    })
    const changeMotion = () => layout(true)
    observer.observe(viewport)
    observer.observe(column)
    document.addEventListener('visibilitychange', resume)
    motion.addEventListener('change', changeMotion)
    layout()

    return () => {
      disposed = true
      observer.disconnect()
      cancelAnimationFrame(frame)
      cancelAnimationFrame(resizeFrame)
      document.removeEventListener('visibilitychange', resume)
      motion.removeEventListener('change', changeMotion)
      Composite.clear(engine.world, false)
      Engine.clear(engine)
    }
  }, [contentKey, loading])

  return { viewportRef, stageRef }
}
