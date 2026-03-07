/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-07 16:49:59 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-07 17:14:57
 */
import { useEffect, useState, type FC } from 'react';
import { Select, Flex, Space } from 'antd';
import type { SelectProps } from 'antd/es/select';
import { useTranslation } from 'react-i18next';

import { getModelList } from '@/api/models';
import type { Query, Model } from '@/views/ModelManagement/types';
import { getListLogoUrl } from '@/views/ModelManagement/utils';
import Tag from '@/components/Tag';

/** Extends AntD SelectProps; omits filterOption since it's handled internally */
interface ModelSelectProps extends SelectProps {
  /** Extra query params passed to getModelList */
  params?: Query;
  placeholder?: string;
}

const ModelSelect: FC<ModelSelectProps> = ({
  params,
  placeholder,
  ...props
}) => {
  const { t } = useTranslation();
  const [options, setOptions] = useState<Model[]>([]);

  // Fetch active models whenever params change; stringify for stable deep comparison
  useEffect(() => {
    getModelList({
      ...(params ?? {}),
      pagesize: 100,
      is_active: true
    }).then((res) => {
      setOptions((res as { items: Model[] }).items ?? []);
    });
  }, [JSON.stringify(params)]);

  // Render the selected value inside the trigger with logo + truncated name
  const labelRender: SelectProps['labelRender'] = ({ value }) => {
    const item = options.find((o) => o.id === value);
    if (!item) return undefined;
    const logo = getListLogoUrl(item.provider, item.logo as string);
    return (
      <Flex align="center" gap={8}>
        {logo && <img src={logo} className="rb:size-5 rb:rounded-md" alt="" />}
        <div className="rb:flex-1 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{item.name}</div>
      </Flex>
    );
  };

  return (
    <Select
      placeholder={placeholder ?? t('common.pleaseSelect')}
      options={options}
      fieldNames={{ label: 'name', value: 'id' }}
      allowClear
      popupMatchSelectWidth={false}
      labelRender={labelRender}
      // Each dropdown option shows logo, name, and capability tags
      optionRender={(option) => {
        const { data } = option;
        const logo = getListLogoUrl(data.provider, data.logo as string);
        return (
          <Flex align="center" gap={8}>
            <Flex align="center" gap={8}>
              {logo && <img src={logo} className="rb:size-5 rb:rounded-md" alt="" />}
              <span className="rb:wrap-break-word rb:line-clamp-1">{data.name as string}</span>
            </Flex>
            {data.capability?.length > 0 && (
              <Space size={4}>
                {data.capability.map((vo: string) => <Tag key={vo}>{vo}</Tag>)}
              </Space>
            )}
          </Flex>
        );
      }}
      {...props}
    />
  );
};

export default ModelSelect;
