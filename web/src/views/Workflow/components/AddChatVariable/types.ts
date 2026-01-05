import type { ChatVariable } from '../../types'

export interface AddChatVariableProps {
  variables?: ChatVariable[];
  onChange?: (variables: ChatVariable[]) => void;
  disabled?: boolean;
  maxVariables?: number;
}

export interface VariableFormData {
  name: string;
  type: ChatVariable['type'];
  description?: string;
  required?: boolean;
  defaultValue?: any;
}

export interface ChatVariableModalRef {
  handleOpen: (value?: ChatVariable, index?: number) => void;
}

export interface ChatVariableModalRef {
  handleOpen: (vo?: ChatVariable, index?: number) => void;
}