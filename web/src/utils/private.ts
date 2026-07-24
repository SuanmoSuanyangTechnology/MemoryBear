import * as MemoryBrick from '@redbear/memory-brick'

/**
 * Whether the real private package @redbear/memory-brick is available.
 * When the package is missing it is replaced by the virtual-memory-brick plugin with a
 * fallback module carrying an __isFallback flag; this lets us decide synchronously at build
 * time without an async import, avoiding render flicker.
 */
export const isPrivateAvailable = !(MemoryBrick as unknown as { __isFallback?: boolean }).__isFallback
