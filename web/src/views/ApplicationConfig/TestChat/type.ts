import type { Application } from '@/views/ApplicationManagement/types'
import type { Config } from '../types';
import type { WorkflowConfig } from '@/views/Workflow/types';

export interface TestChatProps {
  application?: Application | null;
  config: Config | WorkflowConfig | null
}