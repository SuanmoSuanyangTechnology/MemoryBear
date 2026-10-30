import { forwardRef, useImperativeHandle, useState } from 'react'
import { Flex, Skeleton, Tree } from 'antd'
import type { DataNode } from 'antd/es/tree'
import { getSkillIds, getPluginMembers, groupSkills } from './selection'
import pluginIcon from '@/assets/images/menuNew/application_purple.svg'
import skillIcon from '@/assets/images/menuNew/skills.svg'
import { useTranslation } from 'react-i18next'
import { DownOutlined } from '@ant-design/icons';

import type { SkillModalRef } from './types'
import { Kind, type Skill, type SkillQuery } from '@/views/Skills/types'
import RbModal from '@/components/RbModal'
import { getSkillListUrl } from '@/api/skill'
import PageScrollList from '@/components/PageScrollList'
import SearchInput from '@/components/SearchInput'
import PageTabs from '@/components/PageTabs'
import Empty from '@/components/Empty'
type SkillNode = DataNode & { skill: Skill; parent?: Skill; children?: SkillNode[] }

interface SkillModalProps {
  refresh: (rows: Skill[], type: 'skill') => void;
  selectedList: Skill[];
}

type FilterKind = Kind | 'all'

const SkillListModal = forwardRef<SkillModalRef, SkillModalProps>(({ refresh, selectedList }, ref) => {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(false)
  const [kind, setKind] = useState<FilterKind>('all')
  const [search, setSearch] = useState<string | undefined>(undefined)
  const [selectedRows, setSelectedRows] = useState<Skill[]>([])

  const handleClose = () => {
    setVisible(false)
    setSelectedRows([])
  }
  const handleOpen = () => {
    setKind('all')
    setSearch(undefined)
    setSelectedRows([])
    setVisible(true)
  }
  useImperativeHandle(ref, () => ({ handleOpen, handleClose }))

  const addedIds = new Set(getSkillIds(selectedList))
  const selectedIds = new Set([...addedIds, ...getSkillIds(selectedRows)])
  const selectable = (item: Skill) => !!item.id && item.is_active !== false && !addedIds.has(item.id)
  const toggle = (item: Skill, parent?: Skill) => {
    const targets = (item.kind === Kind.Plugin ? getPluginMembers(item) : [item]).filter(selectable)
    if (!targets.length || item.is_active === false) return
    const remove = targets.every(target => selectedIds.has(target.id))
    const ids = new Set(targets.map(target => target.id))
    setSelectedRows(rows => {
      const leaves = rows.flatMap(row => row.kind === Kind.Plugin ? getPluginMembers(row) : [row])
      return groupSkills([...leaves.filter(row => !ids.has(row.id)), ...(remove ? [] : targets)],
        [...rows.filter(row => row.kind === Kind.Plugin), ...(item.kind === Kind.Plugin ? [item] : parent ? [parent] : [])])
    })
  }

  return (
    <RbModal
      title={t('application.addSkill')}
      open={visible}
      onCancel={handleClose}
      okText={t('application.addSkill')}
      okButtonProps={{ disabled: selectedRows.length === 0 }}
      onOk={() => { refresh(selectedRows, 'skill'); handleClose() }}
      width={640}
    >
      <Flex vertical gap={16} className='rb:max-h-[calc(100vh-202px)] rb:overflow-hidden'>
        <Flex align="center" justify="space-between" wrap gap={12}>
          <PageTabs
            value={kind}
            options={[
              { value: 'all', label: t('application.skillAll') },
              { value: Kind.Plugin, label: t('application.skillPlugin') },
              { value: Kind.Skill, label: t('application.skillOnly') },
            ]}
            onChange={value => {
              setKind(value as FilterKind)
              setSearch(undefined)
            }}
          />
          <SearchInput
            key={visible ? 'open' : 'closed'}
            value={search}
            placeholder={t('application.searchPluginOrSkill')}
            onSearch={setSearch} variant="outlined"
          />
        </Flex>
        {visible &&
          <PageScrollList<Skill, SkillQuery>
            url={getSkillListUrl}
            query={{ kind, search: search?.trim() || undefined, is_active: true }}
            heightClass="rb:min-h-60 rb:max-h-[min(60vh,560px)]"
            pageLoading={
              <div aria-hidden="true">
                {Array.from({ length: 6 }, (_, index) => (
                  <div key={index} className="rb:flex rb:items-center rb:gap-2 rb:min-h-11">
                    <Skeleton.Button active style={{ width: 16, minWidth: 16, height: 16 }} />
                    <Skeleton.Avatar active shape="square" size={32} />
                    <Skeleton.Input active size="small" style={{ width: index % 2 ? 180 : 240, maxWidth: '100%' }} className="rb:min-w-0 rb:max-w-[60%]" />
                  </div>
                ))}
              </div>
            }
            empty={<Empty subTitle={t('application.noPluginOrSkill')} />}
            renderItems={items => {
              const checked: string[] = []
              const halfChecked: string[] = []
              const makeNode = (skill: Skill, key: string, parent?: Skill): SkillNode => {
                const plugin = skill.kind === Kind.Plugin
                const members = plugin ? getPluginMembers(skill) : []
                const relevant = members.filter(member => member.id && member.is_active !== false)
                const count = relevant.filter(member => selectedIds.has(member.id)).length
                if (plugin ? relevant.length > 0 && count === relevant.length : selectedIds.has(skill.id)) checked.push(key)
                else if (plugin && count > 0) halfChecked.push(key)
                const disabled = skill.is_active === false || (plugin ? !members.some(selectable) : !selectable(skill))
                return {
                  key, skill, parent, disableCheckbox: disabled,
                  title: <div className={`rb:flex rb:min-w-0 rb:flex-1 rb:items-center rb:gap-2 rb:min-h-11 ${disabled ? 'rb:opacity-45 rb:cursor-not-allowed' : ''}`}>
                    <img src={plugin ? pluginIcon : skillIcon} alt="" className="rb:size-8 rb:shrink-0 rb:rounded-lg rb:bg-gray-50 rb:p-1" />
                    <span className="rb:min-w-0 rb:flex-1 rb:truncate" title={skill.name}>{skill.name || skill.id}</span>
                  </div>,
                  children: plugin ? members.map((member, index) => makeNode(member, key + ':' + index, skill)) : undefined,
                }
              }
              const treeData = items.map((item, index) => makeNode(item, 'root:' + item.id + ':' + index))
              return (
                <Tree<SkillNode>
                  blockNode
                  checkable
                  checkStrictly
                  selectedKeys={[]}
                  checkedKeys={{ checked, halfChecked }} treeData={treeData}
                  switcherIcon={<DownOutlined />}
                  className="rb:[&_.ant-tree-treenode]:flex rb:[&_.ant-tree-treenode]:items-center rb:[&_.ant-tree-switcher]:order-last rb:[&_.ant-tree-switcher]:ml-2 rb:[&_.ant-tree-switcher]:flex rb:[&_.ant-tree-switcher]:items-center rb:[&_.ant-tree-switcher]:justify-center rb:[&_.ant-tree-switcher]:relative! rb:[&_.ant-tree-switcher::before]:absolute! rb:[&_.ant-tree-switcher::before]:top-[50%]! rb:[&_.ant-tree-switcher::before]:mt-0! rb:[&_.ant-tree-switcher::before]:transform! rb:[&_.ant-tree-switcher::before]:-translate-y-1/2!"
                  onCheck={(_, info) => toggle(info.node.skill, info.node.parent)}
                  onSelect={(_, info) => toggle(info.node.skill, info.node.parent)}
                />
              )
            }}
          />
        }
      </Flex>
    </RbModal>
  )
})

export default SkillListModal