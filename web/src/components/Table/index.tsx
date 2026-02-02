/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:29:46 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-02 15:32:11
 */
/**
 * RbTable Component
 * 
 * A table component with built-in pagination and API integration:
 * - Automatic data fetching from API
 * - Pagination with customizable page size
 * - Row selection support
 * - Custom empty state
 * - Configurable scroll behavior
 * - Exposes loadData and getList methods via ref
 * 
 * @component
 */

import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Table } from 'antd';
import type { TableProps } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useTranslation } from 'react-i18next';

import { request } from '@/utils/request';
import Empty from '@/components/Empty';

interface TablePaginationConfig { pagesize: number; page: number; }

/** Props interface for Table component */
interface TableComponentProps extends Omit<TableProps, 'pagination'> {
  /** Table column definitions */
  columns: ColumnsType;
  /** API endpoint URL for data fetching */
  apiUrl?: string;
  /** Query parameters for API request */
  apiParams?: Record<string, unknown>;
  /** Pagination configuration or boolean to enable/disable */
  pagination?: boolean | TablePaginationConfig;
  /** Key to use for row identification */
  rowKey: string;
  /** Row selection configuration */
  rowSelection?: TableProps['rowSelection'];
  /** Initial data to display (used when no API) */
  initialData?: Record<string, unknown>[];
  /** Size of empty state icon */
  emptySize?: number;
  /** Custom empty state text */
  emptyText?: string;
  /** Whether to enable scroll */
  isScroll?: boolean;
  /** Custom horizontal scroll width */
  scrollX?: number | string | true;
  /** Custom vertical scroll height */
  scrollY?: number | string;
  /** Key name for current page in API params */
  currentPageKey?: string;
}

/** Ref methods exposed to parent component */
export interface TableRef {
  /** Reload data from first page */
  loadData: () => void;
  /** Fetch data with specific pagination */
  getList: (pageData: TablePaginationConfig) => void;
}

/** Filter out empty or invalid parameters from API request */
const dealSo = (params: any) => {
  let so: any = {}
  Object.keys(params).forEach(key => {
    if (params[key] === '' || (Array.isArray(params[key]) && params[key].length === 0)) {
      return
    }
    so[key] = params[key]
  })

  return so
}

/** Table component with pagination and API integration */
const RbTable = forwardRef<TableRef, TableComponentProps>(({
  columns,
  apiUrl,
  apiParams,
  pagination = true,
  rowKey,
  rowSelection,
  initialData,
  emptySize = 160,
  emptyText,
  isScroll = false,
  scrollX,
  scrollY,
  currentPageKey = 'page',
  ...props
}, ref) => {
  const { t } = useTranslation();
  const [data, setData] = useState<Record<string, unknown>[]>(initialData || [])
  const [loading, setLoading] = useState(false)
  const [currentPagination, setCurrentPagination] = useState({
    page: 1,
    pagesize: 10,
  });
  const [total, setTotal] = useState(0);

  /** Sync initial data when provided without API */
  useEffect(() => {
    if (initialData && !apiUrl) {
      setData(initialData)
    }
  }, [initialData, apiUrl])

  /** Initialize table and load data from first page */
  const loadData = () => {
    if (apiUrl) {
      getList({
        ...currentPagination,
        page: 1
      })
    }
  }
  
  /** Fetch data from API with pagination */
  const getList = (pageData: TablePaginationConfig) => {
    if (!apiUrl) {
      return
    }
    let params = dealSo(apiParams || {})
    if (pagination) {
      setCurrentPagination({
        ...currentPagination,
        ...pageData,
      })
      params = { ...params, ...pageData, [currentPageKey]: pageData.page}
    }
    setLoading(true)
    /** Build query parameters and call API */
    request.get(apiUrl, params)
      .then((res: any) => {
        /** Support two response formats: direct total or total in page object */
        const totalCount = res.page?.total ?? res.total ?? 0;
        setTotal(totalCount)
        setData(Array.isArray(res.items) ? res.items : Array.isArray(res.hosts) ? res.hosts : Array.isArray(res.list) ? res.list : res || [])
        setLoading(false)
      })
      .catch(err => {
        console.log('err', err)
        setLoading(false)
      })
  }
  
  /** Reload data when initialized or apiParams changes */
  useEffect(() => {
    loadData()
  }, [apiParams])

  /** Handle page change event */
  const handlePageChange = (page: number, pagesize: number) => {
    getList({
      page: page,
      pagesize
    })
  }
  
  /** Pagination configuration with i18n support */
  const paginationConfig = pagination ? ({
    ...(typeof pagination === 'object' ? pagination : {}),
    ...currentPagination,
    total,
    onChange: handlePageChange,
    showSizeChanger: true,
    showQuickJumper: true,
    showTotal: (totalCount: number) => t('table.totalRecords', {total: totalCount})
  }) : false;


  /** Expose loadData and getList methods to parent component via ref */
  useImperativeHandle(ref, () => ({
    loadData,
    getList,
  }));

  /** Calculate scroll configuration based on props */
  const getScrollConfig = () => {
    if (!isScroll && !scrollX && !scrollY) return undefined;
    
    const config: { x?: number | string | true; y?: number | string } = {};
    
    /** Only apply horizontal scroll when there is data */
    if (scrollX !== undefined && data.length > 0) {
      config.x = scrollX;
    } else if (isScroll) {
      config.x = 'max-content';
    }
    
    if (scrollY !== undefined) {
      config.y = scrollY;
    } else if (isScroll) {
      config.y = 'calc(100vh - 280px)';
    }
    
    return Object.keys(config).length > 0 ? config : undefined;
  };

  return (
    <Table
      {...props}
      rowKey={rowKey}
      loading={loading}
      columns={columns}
      dataSource={data}
      pagination={paginationConfig}
      rowSelection={rowSelection}
      rowClassName="rb:text-[#5B6167]"
      locale={{ emptyText: <Empty size={emptySize} subTitle={emptyText} /> }}
      scroll={getScrollConfig()}
      tableLayout="auto"
    />
  );
});

export default RbTable;