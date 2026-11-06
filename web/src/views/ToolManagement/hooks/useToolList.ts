import { useState, useEffect } from 'react';
import { getTools } from '@/api/tools';
import type { ToolType, ToolItem, WorkflowToolItem } from '../types';

/**
 * Common hook for fetching tool list data
 * Used by Mcp, Inner, Custom, Workflow components
 */
export function useToolList<T = ToolItem | WorkflowToolItem>(options: {
  toolType: ToolType;
  keyword?: string;
}) {
  const { toolType, keyword } = options;
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<T[]>([]);

  const getData = () => {
    setLoading(true);
    getTools({
      tool_type: toolType,
      name: keyword,
    })
      .then((res) => {
        setData(res as T[]);
      })
      .finally(() => {
        setLoading(false);
      });
  };

  useEffect(() => {
    getData();
  }, [keyword]);

  return { loading, data, getData };
}
