import { type FC, type ReactNode, useState } from 'react'
import { DownOutlined, RightOutlined } from '@ant-design/icons'

import { Kind, type Skill, type Member } from '@/views/Skills/types'
import SkillIcon from '@/views/Skills/components/SkillIcon'

interface SkillRowProps {
  skill: Skill;
  leading?: ReactNode;
  trailing?: ReactNode;
  disabled?: boolean;
  onClick?: () => void;
  renderMember?: (member: Member, index: number) => ReactNode;
}

/** Members are informational unless the caller supplies an interactive renderer. */
const SkillRow: FC<SkillRowProps> = ({ skill, leading, trailing, disabled, onClick, renderMember }) => {
  const [expanded, setExpanded] = useState(false)
  const plugin = skill.kind === Kind.Plugin

  return (
    <div>
      <div
        className={`rb:flex rb:items-center rb:gap-2 rb:min-h-11 ${disabled ? 'rb:opacity-45 rb:cursor-not-allowed' : onClick ? 'rb:cursor-pointer' : ''}`}
        onClick={disabled ? undefined : onClick}
      >
        {leading}
        <SkillIcon plugin={plugin} />
        <span className="rb:flex-1 rb:min-w-0 rb:truncate" title={skill.name}>{skill.name || skill.id}</span>
        {plugin && <button type="button" aria-label={`${expanded ? '收起' : '展开'}${skill.name}的技能`} aria-expanded={expanded}
          onClick={event => { event.stopPropagation(); setExpanded(value => !value) }} className="rb:cursor-pointer rb:text-gray-500 rb:p-2">
          {expanded ? <DownOutlined /> : <RightOutlined />}
        </button>}
        {trailing}
      </div>
      {plugin && expanded && <div className="rb:pl-10">
        {(skill.members ?? []).map((member, index) => renderMember ? renderMember(member, index) : (
          <div key={member.id ?? index} className="rb:flex rb:items-center rb:gap-2 rb:min-h-11 rb:pl-2">
            <SkillIcon />
            <span className="rb:min-w-0 rb:truncate" title={member.name}>{member.name || member.id}</span>
          </div>
        ))}
      </div>}
    </div>
  )
}

export default SkillRow