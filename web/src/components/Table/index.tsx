import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Table } from 'antd';
import type { TableProps } from 'antd';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import { useTranslation } from 'react-i18next';
import { request } from '@/utils/request';
import styles from './index.module.css';
import Empty from '@/components/Empty';

interface TableComponentProps extends Omit<TableProps, 'pagination'> {
  columns: ColumnsType;
  apiUrl?: string;
  apiParams?: Record<string, unknown>;
  pagination?: boolean | TablePaginationConfig;
  rowKey: string;
  rowSelection?: TableProps['rowSelection'];
  initialData?: Record<string, unknown>[];
  emptySize?: number;
  isScroll?: boolean;
  scrollX?: number | string | true; // 支持自定义横向滚动宽度
  scrollY?: number | string; // 支持自定义纵向滚动高度
}
export interface TableRef {
  loadData: () => void;
  getList: (pageData: TablePaginationConfig) => void;
}

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
const TableComponent = forwardRef<TableRef, TableComponentProps>(({
  columns,
  apiUrl,
  apiParams,
  pagination = true,
  rowKey,
  rowSelection,
  initialData,
  emptySize = 160,
  isScroll = false,
  scrollX,
  scrollY,
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

  useEffect(() => {
    if (initialData && !apiUrl) {
      setData(initialData)
    }
  }, [initialData, apiUrl])

  // 数据加载
  // 表格初始化
  const loadData = () => {
    if (apiUrl) {
      getList({
        ...currentPagination,
        page: 1
      })
    }
  }
  // 获取数据
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
      params = {...params, ...pageData}
    }
    setLoading(true)
    // 构建查询参数并调用API
    request.get(apiUrl, params)
      .then((res: any) => {
        // 支持两种响应格式：直接返回 total 或在 page 对象中返回
        const totalCount = res.page?.total ?? res.total ?? 0;
        setTotal(totalCount)
        setData(Array.isArray(res.items) ? res.items : Array.isArray(res.hosts) ? res.hosts : res || [])
        setLoading(false)
      })
      .catch(err => {
        console.log('err', err)
        setLoading(false)
      })
  }
  // 初始化和apiParams变化时重新加载数据
  useEffect(() => {
    loadData()
  }, [apiParams])

  // 分页相关
  // 切换分页
  const handlePageChange = (page: number, pagesize: number) => {
    getList({
      page: page,
      pagesize
    })
  }
  // 分页配置
  const paginationConfig = pagination ? ({
    ...(typeof pagination === 'object' ? pagination : {}),
    ...currentPagination,
    total,
    onChange: handlePageChange,
    showSizeChanger: true,
    showQuickJumper: true,
    showTotal: (totalCount: number) => t('table.totalRecords', {total: totalCount})
  }) : false;


  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    loadData,
    getList,
  }));

  // 计算 scroll 配置
  const getScrollConfig = () => {
    if (!isScroll && !scrollX && !scrollY) return undefined;
    
    const config: { x?: number | string | true; y?: number | string } = {};
    
    // 只有在有数据时才应用横向滚动
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
      rowClassName={styles.row}
      className={styles.table}
      locale={{ emptyText: <Empty size={emptySize} /> }}
      scroll={getScrollConfig()}
      tableLayout="auto"
    />
  );
});

export default TableComponent;