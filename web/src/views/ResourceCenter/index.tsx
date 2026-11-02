import { useSearchParams } from 'react-router-dom';

import Skills from '@/views/Skills';

type ResourceType = 'agent' | 'skills' | 'tool';
const resourceTypes: ResourceType[] = ['agent', 'skills', 'tool'];
function getResourceType(value: string | null): ResourceType {
  return resourceTypes.find(type => type === value) ?? 'skills';
}

/** Browse existing tenant resources; publishing awaits a backend catalog. */
const ResourceCenter = () => {
  const [params] = useSearchParams();
  const type = getResourceType(params.get('type'));

  if (type === 'skills') return <Skills />;
  return null;
}

export default ResourceCenter