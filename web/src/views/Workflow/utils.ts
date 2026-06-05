/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-24 15:07:49 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-29 16:23:50
 */

import { conditionNodePortItemArgsY, conditionNodeHeight } from './constant'
import type { Application } from '@/views/ApplicationManagement/types';

export const isSubExprSet = (sub: any) => {
  if (!sub?.key) return false;
  if (['not_empty', 'empty'].includes(sub?.operator)) return true;
  return !!sub.value || typeof sub.value === 'boolean' || typeof sub.value === 'number';
};
/**
 * Calculate the total height of a condition (if-else) node based on its cases.
 * Uses the same per-expression height logic as getConditionNodeCasePortY.
 */
export const calcConditionNodeTotalHeight = (cases: any[]) => {
  if (!cases?.length) return conditionNodeHeight;
  const casesHeight = cases.reduce((acc: number, c: any) => {
    const exprs = c?.expressions ?? [];
    const n = exprs.length;
    const exprsHeight = n === 0 ? 0 : exprs.reduce((s: number, e: any) => s + calcExpressionHeight(e), 0) + 2 * (n - 1);
    return acc + 20 + exprsHeight;
  }, 0);
  return conditionNodeHeight + casesHeight + (cases.length - 1) * 4 - 27.5;
};

/**
 * Height of a single expression block in ConditionNode (px).
 *
 * expression outer Flex padding:
 *   - has sub conditions (length > 0): pt-1 (4px top only)
 *   - no sub conditions:              py-1 (4px top + 4px bottom)
 * expression main row: leading-4 = 16px
 * sub_variable_condition block (mt-1 = 4px gap):
 *   - all isSet, m subs: sub[0] = leading-3.5(14) + pb-1(4) = 18px;
 *                        sub[k>0] = py-1(8) + leading-3.5(14) = 22px
 *                        total = 18 + 22*(m-1)
 *   - exists but not all isSet: pb-1(4) + leading-4(16) = 20px
 */
const calcExpressionHeight = (expression: any): number => {
  const subs = expression?.sub_variable_condition?.conditions;
  if (!subs?.length) return 24; // py-1(8) + leading-4(16)
  const subBlockHeight = subs.every(isSubExprSet)
    ? 18 + 22 * (subs.length - 1)
    : 20;
  return 4 + 16 + 4 + subBlockHeight - 2; // pt-1 + main row + mt-1 + sub block (-2 rendering correction)
};

/**
 * Calculate the Y-coordinate of the right-side output port for a specific case
 * in a condition (if-else) node, aligned with the IF/ELIF label in ConditionNode.
 *
 * Layout (from node top):
 * - 12px padding-top + 24px header + 12px mt-3 = 48px to cases area
 * - Each IF/ELIF label row: leading-4 (16px), center at +8px → first port Y = 56.5
 * - Each case: IF/ELIF row (leading-4=16) + mb-1(4) + expressions (gap={2}=2px between)
 * - Gap between cases (Flex gap={4}): 4px
 */
export const getConditionNodeCasePortY = (cases: any[], caseIndex: number) => {
  let y = conditionNodePortItemArgsY; // 56.5, center of first IF label
  for (let i = 0; i < caseIndex; i++) {
    const exprs = cases[i]?.expressions ?? [];
    const n = exprs.length;
    // IF/ELIF row (16) + mb-1 (4) = 20px base; expressions: sum of heights + 2px gap between
    const exprsHeight = n === 0 ? 0 : exprs.reduce((acc: number, e: any) => acc + calcExpressionHeight(e), 0) + 2 * (n - 1);
    y += 20 + exprsHeight + 4; // case height + Flex gap between cases
  }
  return y;
};

export const filterNodeByAppType = (nodeType: { type: string }, appType?: Application['type']) => {
  if (appType === 'pure_workflow') {
    return !['end', 'memory-read', 'memory-write'].includes(nodeType.type);
  }
  if (appType === 'workflow') {
    return !['output', 'trigger'].includes(nodeType.type);
  }
  return true;
};