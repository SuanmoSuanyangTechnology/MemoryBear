import { useState, useImperativeHandle, forwardRef } from 'react'
import { Form, Input, Button, Flex } from 'antd'
import { useTranslation } from 'react-i18next'
import RbModal from '@/components/RbModal'

import VariableSelect from '../../Properties/VariableSelect'
import type { Suggestion } from '../plugin/AutocompletePlugin'

const { Item: FormItem } = Form

type PrefillMode = 'static' | 'variable'

export interface FormFieldEditModalRef {
  open: (initialId?: string, initialDefaultValue?: string, initialVariableRef?: string) => void
  close: () => void
}

interface FormFieldEditModalProps {
  initialId?: string
  initialDefaultValue?: string
  initialVariableRef?: string
  options?: Suggestion[]
  onSave: (id: string, defaultValue?: string, variableRef?: string) => void
}

const FormFieldEditModal = forwardRef<FormFieldEditModalRef, FormFieldEditModalProps>(({
  initialId,
  options = [],
  onSave
}, ref) => {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const [open, setOpen] = useState(false)
  const [editId, setEditId] = useState(initialId)
  const [prefillMode, setPrefillMode] = useState<PrefillMode>('static')

  useImperativeHandle(ref, () => ({
    open: openModal,
    close: onCancel
  }))
  const openModal = (id?: string, defaultValue?: string, variableRef?: string) => {
    setEditId(id || '')
    if (variableRef) {
      setPrefillMode('variable')
    } else {
      setPrefillMode('static')
    }
    form.setFieldsValue({
      id,
      default_value: defaultValue,
      variable_ref: variableRef
    })
    setOpen(true)
  }
  const onCancel = () => {
    setOpen(false)
  }

  const handleSave = () => {
    form.validateFields()
      .then(values => {
        const id = values.id || ''
        if (!id.trim()) return
        
        if (prefillMode === 'static') {
          onSave(id.trim(), values.default_value || undefined, undefined)
        } else {
          onSave(id.trim(), undefined, values.variable_ref || undefined)
        }
      })
  }

  const handleModeChange = (mode: PrefillMode) => {
    setPrefillMode(mode)
    if (mode === 'static') {
      form.setFieldsValue({ variable_ref: '' })
    } else {
      form.setFieldsValue({ default_value: '' })
    }
  }

  return (
    <RbModal
      title={editId ? t('workflow.formFieldEdit') : t('workflow.formFieldAdd')}
      open={open}
      onCancel={onCancel}
      onOk={handleSave}
    >
      <Form form={form} layout="vertical">
        <FormItem
          name="id"
          label={t('workflow.saveResponseAs')}
          rules={[{ required: true, message: '' }]}
        >
          <Input
            placeholder={t('common.pleaseEnter')}
            autoFocus
          />
        </FormItem>

        <Flex align="center" gap={8} className="rb:mb-2">
          <Button 
            type="link"
            size="small" 
            className={prefillMode === 'static' ? '' : 'text-blue-500'}
            onClick={() => handleModeChange('static')}
          >
            {t('workflow.addStaticContent')}
          </Button>
          {t('workflow.or')}
          <Button 
            type="link"
            size="small"
            onClick={() => handleModeChange('variable')}
          >
            {`{x}`} {t('workflow.addVariable')}
          </Button>
        </Flex>
        {prefillMode === 'static' && (
          <FormItem
            name="default_value"
            label={t('workflow.prefillField')}
          >
            <Input
              placeholder={t('workflow.prefillFieldPlaceholder')}
            />
          </FormItem>
        )}
        {prefillMode === 'variable' && (
          <FormItem
            name="variable_ref"
            label={t('workflow.prefillField')}
          >
            <VariableSelect
              placeholder={t('workflow.selectVariable')}
              options={options}
            />
          </FormItem>
        )}
      </Form>
    </RbModal>
  )
})

export default FormFieldEditModal
