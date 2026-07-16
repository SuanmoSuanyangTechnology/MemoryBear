import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'

interface SubCondition {
  key: string;
  operator: string;
  value?: string | number;
  input_type?: string;
}

export interface SubVariableCondition {
  logical_operator: 'and' | 'or';
  conditions: SubCondition[];
}

export interface Expression {
  left?: string;
  operator?: string;
  right?: string | number;
  input_type?: string;
  sub_variable_condition?: SubVariableCondition;
}

export interface CaseItem {
  logical_operator: 'and' | 'or';
  expressions: Expression[];
}
export interface CaseListProps {
  value?: CaseItem[];
  onChange?: (value: CaseItem[]) => void;
  options: Suggestion[];
  name: string;
  selectedNode?: any;
  graphRef?: any;
}

export interface ArrayFileSubConditionsProps {
  conditionFieldName: number;
  caseIndex: number;
  conditionIndex: number;
  name: string;
  filterNumberOptions: Suggestion[];
  options: Suggestion[];
  updateNodeLayout: (cases: any[]) => void;
  updateNodePorts: (caseCount: number, removedCaseIndex?: number) => void;
}