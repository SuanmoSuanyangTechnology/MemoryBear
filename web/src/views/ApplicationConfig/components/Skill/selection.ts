import { Kind, type Skill } from '@/views/Skills/types'

/** Plugin objects are display containers; only executable skill IDs are saved. */
export const getSkillIds = (rows: (Skill | string)[] = []): string[] => [...new Set(rows.flatMap(row =>
  typeof row === 'string' ? [row] : row.kind === Kind.Plugin
    ? (row.members ?? []).flatMap(member => member.id ? [member.id] : [])
    : row.id ? [row.id] : []
))]

export const getPluginMembers = (plugin: Skill): Skill[] => (plugin.members ?? []).map(member => ({
  ...plugin,
  ...member,
  id: member.id ?? '',
  name: member.name || member.id || '',
  description: member.description ?? '',
  kind: Kind.Skill,
  plugin_id: plugin.id,
  members: undefined,
  is_active: member.is_active ?? true,
}))

/** Keep only selected members in each display container. */
export const groupSkills = (rows: (Skill | string)[], plugins: Skill[] = []): Skill[] => {
  const containers = new Map(plugins.map(plugin => [plugin.id, plugin]))
  rows.forEach(row => { if (typeof row !== 'string' && row.kind === Kind.Plugin) containers.set(row.id, row) })
  const leaves = rows.flatMap(row => typeof row === 'string' ? [{ id: row } as Skill]
    : row.kind === Kind.Plugin ? getPluginMembers(row) : [row])
  const result = new Map<string, Skill>()
  const seen = new Set<string>()
  leaves.forEach(skill => {
    if (!skill.id || seen.has(skill.id)) return
    seen.add(skill.id)
    if (!skill.plugin_id) { result.set(`skill:${skill.id}`, skill); return }
    const key = `plugin:${skill.plugin_id}`
    const group = result.get(key) ?? {
      ...skill, ...containers.get(skill.plugin_id), id: skill.plugin_id,
      name: containers.get(skill.plugin_id)?.name || skill.plugin_id,
      kind: Kind.Plugin, members: [],
    }
    group.members = [...(group.members ?? []), { ...skill, has_scripts: skill.has_scripts ?? undefined, package_id: skill.package_id ?? undefined }]
    result.set(key, group)
  })
  return [...result.values()]
}
