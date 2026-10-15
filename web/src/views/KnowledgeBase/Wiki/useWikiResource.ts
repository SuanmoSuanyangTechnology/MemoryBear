import { useCallback, useEffect, useRef, useState } from 'react';

export function wikiErrorMessage(error: unknown): string {
  const value = error as { response?: { data?: { msg?: string; error?: string } }; msg?: string; message?: string };
  return value?.response?.data?.msg || value?.response?.data?.error || value?.msg || value?.message || '加载失败，请稍后重试。';
}

/** Cancel obsolete reads so a previous page cannot replace the current selection. */
export function useWikiResource<T>(load: (signal: AbortSignal) => Promise<T>, pollInterval = 0, shouldPoll?: (data: T) => boolean) {
  const [state, setState] = useState<{ data?: T; loading: boolean; error?: string }>({ loading: true });
  const [revision, setRevision] = useState(0);
  const loader = useRef(load);
  loader.current = load;
  const reload = useCallback(() => setRevision(value => value + 1), []);
  const pollEnabled = state.data === undefined || !shouldPoll || shouldPoll(state.data);

  useEffect(() => {
    const controller = new AbortController();
    setState({ loading: true });
    load(controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ data, loading: false });
    }).catch(error => {
      if (!controller.signal.aborted) setState({ loading: false, error: wikiErrorMessage(error) });
    });
    return () => controller.abort();
  }, [load, revision]);

  useEffect(() => {
    if (!pollInterval || !pollEnabled || state.loading || state.error) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (document.visibilityState === 'visible') {
        try {
          const data = await loader.current(controller.signal);
          if (!controller.signal.aborted) setState({ data, loading: false });
        } catch (error) {
          if (!controller.signal.aborted) {
            // Withdraw stale content if the source or access disappeared during polling.
            setState({ loading: false, error: wikiErrorMessage(error) });
          }
          return;
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, pollInterval);
    };
    timer = setTimeout(poll, pollInterval);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [load, pollEnabled, pollInterval, revision, state.loading, state.error]);

  return { ...state, reload };
}
