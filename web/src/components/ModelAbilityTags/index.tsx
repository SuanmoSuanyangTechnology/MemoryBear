import { useTranslation } from 'react-i18next'
import Tag from '@/components/Tag'
import OverflowTags from '@/components/OverflowTags';

/** Uses only the model v2 contract; text is hidden only in the presentation. */
export default function ModelAbilityTags({ input_modalities = [], output_modalities = [], features = [] }: {
  input_modalities?: readonly string[];
  output_modalities?: readonly string[];
  features?: readonly string[];
}) {
  const { t } = useTranslation()
  const groups = [
    { key: 'input_modalities', values: input_modalities, color: 'processing' as const },
    { key: 'output_modalities', values: output_modalities, color: 'success' as const },
    { key: 'features', values: features, color: 'purple' as const },
  ]
  return <span className="rb:inline-flex rb:flex-col">
    {groups.map(group => {
      const values = [...new Set(group.values)]
      if (values.length === 0) return null
      const label = t(`modelNew.${group.key}`)
      return (
        <span key={group.key} role="group" aria-label={label} className="rb:flex rb:items-start rb:gap-2">
          <span className="rb:shrink-0 rb:text-xs rb:leading-6 rb:text-gray-600">{label}: </span>
          <OverflowTags
            items={values.map(value => <Tag key={value} color={group.color} size="small">{t(`modelNew.${value}`)}</Tag>)}
          />
        </span>
      )
    })}
  </span>
}
