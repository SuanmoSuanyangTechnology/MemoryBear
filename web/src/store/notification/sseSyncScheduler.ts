interface SSESyncScheduler {
  schedule: (targetCursor: string | null) => void;
  clear: () => void;
}

export const createSSESyncScheduler = (
  windowMs: number,
  onFlush: (targetCursor: string | null) => void,
): SSESyncScheduler => {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pendingTargetCursor: string | null = null;

  const clear = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    pendingTargetCursor = null;
  };

  const schedule = (targetCursor: string | null) => {
    if (targetCursor) pendingTargetCursor = targetCursor;
    if (timer) clearTimeout(timer);

    timer = setTimeout(() => {
      timer = null;
      const cursor = pendingTargetCursor;
      pendingTargetCursor = null;
      onFlush(cursor);
    }, windowMs);
  };

  return { schedule, clear };
};
