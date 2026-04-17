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
  const editorKeyRef = useRef(0)
  const insertedVarsRef = useRef<Set<string>>(new Set())

  // Collect variables inserted via autocomplete
  useEffect(() => {
    const handler = (e: Event) => {
      insertedVarsRef.current.add((e as CustomEvent).detail.value)
    }
    document.addEventListener('jinja2-variable-inserted', handler)
    return () => document.removeEventListener('jinja2-variable-inserted', handler)
  }, [])

  // Reset refs when node changes
  useEffect(() => {
    if (selectedNode?.getData()?.id) {
      prevMappingNamesRef.current = []
      prevTemplateVarsRef.current = []
    }
  }, [selectedNode?.getData()?.id])

  // Sync template when mapping names change
  useEffect(() => {
    if (
      isSyncingRef.current ||
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
      prevTemplateVarsRef.current = extractTemplateVars(updatedTemplate)
      prevMappingNamesRef.current = currentMappingNames
      form.setFieldValue('template', updatedTemplate)
      editorKeyRef.current++

      setTimeout(() => { isSyncingRef.current = false }, 0)
    } else {
      prevMappingNamesRef.current = currentMappingNames
    }
  }, [values?.mapping, selectedNode?.data?.type, form])

  // Track template vars; add mapping only for autocomplete-inserted variables
  useEffect(() => {
    if (isSyncingRef.current || selectedNode?.data?.type !== 'jinja-render' || !values?.template) return

    const templateVars = extractTemplateVars(String(values.template))
    const prevVars = prevTemplateVarsRef.current

    if (JSON.stringify(prevVars) === JSON.stringify(templateVars)) return

    const newVars = templateVars.filter(v => !prevVars.includes(v))
    const insertedNew = newVars.filter(v => insertedVarsRef.current.has(v))
    insertedVarsRef.current.clear()

    prevTemplateVarsRef.current = templateVars

    if (insertedNew.length === 0 || !values?.mapping) return

    const updatedMapping: MappingItem[] = Array.isArray(values.mapping)
      ? [...values.mapping.filter((item: MappingItem) => item)]
      : []
    let updatedTemplate = String(values.template)

    insertedNew.forEach(varName => {
      const alreadyExists = updatedMapping.some(item => item.value === `{{${varName}}}`)
      const baseName = varName.includes('.') ? varName.split('.').pop()! : varName
      const regex = new RegExp(`{{\\s*${varName.replace(/\./, '\\.')}\\s*}}`, 'g')
      if (alreadyExists) {
        const existing = updatedMapping.find(item => item.value === `{{${varName}}}`)!
        updatedTemplate = updatedTemplate.replace(regex, `{{${existing.name}}}`)
        return
      }
      const usedNames = getMappingNames(updatedMapping)
      let mappingName = baseName
      let counter = 1
      while (usedNames.includes(mappingName)) mappingName = `${baseName}_${counter++}`
      updatedMapping.push({ name: mappingName, value: `{{${varName}}}` })
      updatedTemplate = updatedTemplate.replace(regex, `{{${mappingName}}}`)
    })

    isSyncingRef.current = true
    prevMappingNamesRef.current = getMappingNames(updatedMapping)
    prevTemplateVarsRef.current = extractTemplateVars(updatedTemplate)
    form.setFieldValue('mapping', updatedMapping)
    form.setFieldValue('template', updatedTemplate)
    editorKeyRef.current++
    setTimeout(() => { isSyncingRef.current = false }, 0)
  }, [values?.template, selectedNode?.data?.type, form])

  return (
    <>
      <Form.Item name="mapping">
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
          className="rb:bg-[#F6F6F6] rb:border-[#F6F6F6]! rb:hover:bg-white rb:hover:border-[#171719]!"
        />
      </Form.Item>
    </>
  )
}

export default JinjaRender
