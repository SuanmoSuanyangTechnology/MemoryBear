import { type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Input, Button, Form, Space } from 'antd';
import { PlusOutlined, CopyOutlined, DeleteOutlined, ExpandOutlined } from '@ant-design/icons';

interface CategoryListProps {
  parentName: string;
}

const CategoryList: FC<CategoryListProps> = ({ parentName }) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const formValues = Form.useWatch([parentName], form);

  console.log('formValues', formValues)
  return (
    <Form.List name={parentName}>
      {(fields, { add, remove }) => (
        <Space direction="vertical" size={12} className="rb:w-full">
          {fields.map(({ key, name, ...restField }, index) => {
            const currentItem = formValues?.[key] || {};
            const contentLength = (currentItem.class_name || '').length;
            
            return (
            <div key={key} className="rb:border rb:border-[#DFE4ED] rb:rounded-md rb:p-3 rb:bg-[#F8F9FB]">
              <div className="rb:flex rb:items-center rb:justify-between rb:mb-2">
                <div>{t('workflow.config.question-classifier.class_name')} {index + 1}</div>
                <div className="rb:flex rb:items-center rb:gap-1">
                  <span className="rb:text-xs rb:text-gray-500">{contentLength}</span>
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined />}
                      onClick={() => remove(name)}
                  />
                </div>
              </div>
              <Form.Item
                {...restField}
                  name={[name, 'class_name']}
                noStyle
              >
                <Input.TextArea
                  placeholder={t('common.pleaseEnter')}
                  rows={2}
                />
              </Form.Item>
            </div>
          )})}
          
          <Button
            type="dashed"
            onClick={() => add({})}
            className="rb:w-full"
          >
            + {t('workflow.config.question-classifier.addClassName')}
          </Button>
        </Space>
      )}
    </Form.List>
  );
};

export default CategoryList;