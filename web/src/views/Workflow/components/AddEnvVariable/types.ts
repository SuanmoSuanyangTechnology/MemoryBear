import type { EnvVariable } from '../../types';

export interface AddEnvVariableProps {
  variables?: EnvVariable[];
  onChange?: (variables: EnvVariable[]) => void;
  disabled?: boolean;
  maxVariables?: number;
}

export type EnvVariableFormData = EnvVariable

export interface EnvVariableModalRef {
  handleOpen: (value?: EnvVariable, index?: number) => void;
}