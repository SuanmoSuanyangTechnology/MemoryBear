import { type FC, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Form } from 'antd'
import { Node } from '@antv/x6'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import MappingList from '../MappingList'
import MessageEditor from '../MessageEditor'

interface MappingItem {
  name?: string
  value?: string
}

interface JinjaRenderProps {
  options: Suggestion[]
  templateOptions: Suggestion[]
  selectedNode: Node
}

const extractTemplateVars = (template: string): string[] => {
  return (template.match(/{{\s*([\w.]+)\s*}}/g) || [])
    .map(m => m.replace(/{{\s*|\s*}}/g, ''))
}

const getMappingNames = (mapping: MappingItem[]): string[] => {
  return mapping.filter(item => item?.name).map(item => item.name!)
}

const JinjaRender: FC<JinjaRenderProps> = ({ selectedNode, options, templateOptions }) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const values = Form.useWatch([], form) || {}

  const prevMappingNamesRef = useRef<string[]>([])
  const prevTemplateVarsRef = useRef<string[]>([])
  const isSyncingRef = useRef(false)
  const lastSyncSourceRef = useRef<'mapping' | 'template' | null>(null)
  const editorKeyRef = useRef(0)

  // Reset refs when node changes
  useEffect(() => {
    if (selectedNode?.getData()?.id) {
      prevMappingNamesRef.current = []
      prevTemplateVarsRef.current = []
      lastSyncSourceRef.current = null
    }
  }, [selectedNode?.getData()?.id])

  // Sync template when mapping names change
  useEffect(() => {
    if (
      isSyncingRef.current ||
      lastSyncSourceRef.current === 'mapping' ||
      selectedNode?.data?.type !== 'jinja-render' ||
      !values?.mapping ||
      !values?.template
    ) return

    const currentMappingNames = Array.isArray(values.mapping) ? getMappingNames(values.mapping) : []
    const prevNames = prevMappingNamesRef.current

    if (prevNames.length === 0) {
      prevMappingNamesRef.current = currentMappingNames
      return
    }

    if (JSON.stringify(prevNames) === JSON.stringify(currentMappingNames)) return

    let updatedTemplate = String(form.getFieldValue('template') || '')

    prevNames.forEach((oldName, index) => {
      const newName = currentMappingNames[index]
      if (newName && oldName !== newName) {
        updatedTemplate = updatedTemplate.replace(
          new RegExp(`{{\\s*${oldName}\\s*}}`, 'g'),
          `{{${newName}}}`
        )
      }
    })


    if (updatedTemplate !== form.getFieldValue('template')) {
      isSyncingRef.current = true
      lastSyncSourceRef.current = 'mapping'
      
      prevTemplateVarsRef.current = extractTemplateVars(updatedTemplate)
      prevMappingNamesRef.current = currentMappingNames
      form.setFieldValue('template', updatedTemplate)
      editorKeyRef.current++

      setTimeout(() => {
        isSyncingRef.current = false
        lastSyncSourceRef.current = null
      }, 0)
    } else {
      prevMappingNamesRef.current = currentMappingNames
    }
  }, [values?.mapping, selectedNode?.data?.type, form])

  // Sync mapping when template variables change
  useEffect(() => {
    if (
      isSyncingRef.current ||
      lastSyncSourceRef.current === 'template' ||
      selectedNode?.data?.type !== 'jinja-render' ||
      !values?.template ||
      !values?.mapping
    ) return

    const templateVars = extractTemplateVars(String(values.template))
    if (JSON.stringify(prevTemplateVarsRef.current) === JSON.stringify(templateVars)) return

    const isTemplateEditor = document.activeElement?.closest('[data-editor-type="template"]')
    if (!isTemplateEditor) {
      prevTemplateVarsRef.current = templateVars
      return
    }

    const updatedMapping: MappingItem[] = Array.isArray(values.mapping)
      ? [...values.mapping.filter((item: MappingItem) => item)]
      : []
    const existingNames = getMappingNames(updatedMapping)
    let updatedTemplate = String(values.template)

    // Update existing mapping names based on position
    if (prevTemplateVarsRef.current.length > 0) {
      prevTemplateVarsRef.current.forEach((oldVar, index) => {
        const newVar = templateVars[index]
        if (newVar && oldVar !== newVar && updatedMapping[index]) {
          updatedMapping[index] = { ...updatedMapping[index], name: newVar }
        }
      })
    }

    // Add new mappings and normalize template
    templateVars.forEach(varName => {
      const existingMapping = updatedMapping.find(item => item.value === `{{${varName}}}`)
      const regex = new RegExp(`{{\\s*${varName.replace(/\./g, '\\.')}\\s*}}`, 'g')

      if (existingMapping) {
        updatedTemplate = updatedTemplate.replace(regex, `{{${existingMapping.name}}}`)
      } else if (!existingNames.includes(varName)) {
        const mappingName = varName.includes('.') ? varName.split('.').pop() || varName : varName
        updatedMapping.push({ name: mappingName, value: `{{${varName}}}` })
        updatedTemplate = updatedTemplate.replace(regex, `{{${mappingName}}}`)
      }
    })

    // Remove duplicates only
    const seenNames = new Set<string>()
    const finalMapping = updatedMapping.filter(item => {
      if (!item.name || seenNames.has(item.name)) return false
      seenNames.add(item.name)
      return true
    })

    isSyncingRef.current = true
    lastSyncSourceRef.current = 'template'
    prevMappingNamesRef.current = getMappingNames(finalMapping)
    prevTemplateVarsRef.current = templateVars

    if (JSON.stringify(finalMapping) !== JSON.stringify(values.mapping)) {
      form.setFieldValue('mapping', finalMapping)
    }
    if (updatedTemplate !== String(values.template)) {
      form.setFieldValue('template', updatedTemplate)
    }

    setTimeout(() => {
      isSyncingRef.current = false
      lastSyncSourceRef.current = null
    }, 50)
  }, [values?.template, selectedNode?.data?.type, form])

  return (
    <>
      <Form.Item name="mapping" noStyle>
        <MappingList label={t('workflow.config.jinja-render.mapping')} name="mapping" options={options} />
      </Form.Item>

      <Form.Item name="template">
        <MessageEditor
          key={editorKeyRef.current}
          title={t('workflow.config.jinja-render.template')}
          isArray={false}
          parentName="template"
          language="jinja2"
          options={templateOptions}
          titleVariant="borderless"
          size="small"
        />
      </Form.Item>
    </>
  )
}

export default JinjaRender
