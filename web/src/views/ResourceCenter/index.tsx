import { useSearchParams } from 'react-router-dom';

import Skills from '@/views/Skills';
import Tools from '@/views/ToolManagement';
import Applications from './Applications';

type ResourceType = 'apps' | 'skills' | 'tools';
const resourceTypes: ResourceType[] = ['apps', 'skills', 'tools'];
function getResourceType(value: string | null): ResourceType {
  return resourceTypes.find(type => type === value) ?? 'apps';
}

/** Browse tenant resources; applications use an isolated mock catalog. */
const ResourceCenter = () => {
  const [params] = useSearchParams();
  const type = getResourceType(params.get('type'));

  if (type === 'skills') return <Skills />;
  if (type === 'tools') return <Tools />;
  return <Applications />;
}

export default ResourceCenter