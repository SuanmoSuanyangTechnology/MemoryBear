import { useEffect, useState, type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Cascader } from 'antd'
import type { CascaderProps } from 'antd';
import { getModelProviderList } from '@/api/models'

interface Option {
  value?: string | number | null;
  label: React.ReactNode;
  children?: Option[];
  isLeaf?: boolean;
}
const CustomSelect: FC<CascaderProps> = () => {
  const { t } = useTranslation();
  const [options, setOptions] = useState<Option[]>([]); 
  useEffect(() => {
    getProviderList()
  }, []);

  const getProviderList = () => {
    getModelProviderList().then(res => {
      const response = res as string[]
      setOptions(response.map((key: string) => ({
        value: key,
        label: t(`model.${key}`),
        children: [],
        isLeaf: false,
      })))
    })
  }
  const loadData = (selectedOptions: Option[]) => {
    const targetOption = selectedOptions[selectedOptions.length - 1];
    console.log(targetOption)
  }
  return (
    <Cascader 
      options={options} 
      loadData={loadData}
      changeOnSelect 
    />
  );
}
export default CustomSelect;