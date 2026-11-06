import { useEffect, useRef, useState } from 'react';
import { getToolDetail, getToolMethods } from '@/api/tools';
import type { ToolDetail, ToolMethod } from '../toolContract';
import type { ToolItem } from '../types';

export function useCustomToolDetail() {
  const [visible, setVisible] = useState(false);
  const [detail, setDetail] = useState<ToolItem | ToolDetail | null>(null);
  const [methods, setMethods] = useState<ToolMethod[]>([]);
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);

  useEffect(() => () => {
    generation.current += 1;
  }, []);

  const close = () => {
    generation.current += 1;
    setVisible(false);
  };

  const open = (item: ToolItem) => {
    const current = ++generation.current;
    setDetail(item);
    setMethods([]);
    setLoading(true);
    setVisible(true);
    return Promise.all([getToolDetail(item.id), getToolMethods(item.id)])
      .then(([response, methodResponse]) => {
        if (current !== generation.current) return;
        setDetail(response);
        setMethods(methodResponse);
      })
      .finally(() => {
        if (current === generation.current) setLoading(false);
      });
  };

  return { visible, detail, methods, loading, open, close };
}
