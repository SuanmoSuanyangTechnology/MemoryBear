/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-24 15:07:49 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-17 20:40:47
 */

import { portItemArgsY, conditionNodePortItemArgsY, conditionNodeHeight } from './constant'

/**
 * Calculate the total height of a condition (if-else) node based on its cases.
 *
 * The height is composed of:
 * - `conditionNodeHeight`: the base height of the node (header + padding).
 * - `(cases.length - 1) * 26`: vertical spacing added for each additional case
 *   beyond the first (each case separator row is 26px).
 * - `exprCount * 20`: each individual expression row occupies 20px.
 * - `hasMultiExprCount * 3`: a small extra padding (3px per expression) is added
 *   for cases that contain more than one expression, to account for the logical
 *   operator indicator (AND/OR) between expressions.
 *
 * @param cases - Array of case objects, each containing an `expressions` array.
 * @returns The total pixel height for the condition node.
 */
export const isSubExprSet = (sub: any) => {
  if (!sub?.key) return false;
  if (['not_empty', 'empty'].includes(sub?.operator)) return true;
  return !!sub.value || typeof sub.value === 'boolean' || typeof sub.value === 'number';
};

const getEffectiveExprCount = (expr: any): number => {
  const subs = expr?.sub_variable_condition?.conditions;
  if (subs?.length && subs.every(isSubExprSet)) return 1 + subs.length;
  if (subs?.length > 0) {
    return 2
  }
  return 1;
};

export const calcConditionNodeTotalHeight = (cases: any[]) => {
  // Total number of effective expression rows (sub_variable_condition expand height when all set)
  const exprCount = cases.reduce((acc: number, c: any) =>
    acc + (c?.expressions?.reduce((s: number, e: any) => s + getEffectiveExprCount(e), 0) || 0), 0);
  // Sum of effective expression counts only for cases that have more than one expression
  const hasMultiExprCount = cases.reduce((acc: number, c: any) => {
    if (!c?.expressions?.length || c.expressions.length <= 1) return acc;
    const effectiveCount = c.expressions.reduce((s: number, e: any) => s + getEffectiveExprCount(e), 0);
    return acc + effectiveCount;
  }, 0);

  return conditionNodeHeight + (cases.length - 1) * 26 + exprCount * 20 + hasMultiExprCount * 3;
};

/**
 * Calculate the Y-coordinate of the right-side output port for a specific case
 * in a condition (if-else) node.
 *
 * The port position is determined by iterating through all preceding cases
 * (index 0 to caseIndex - 1) and accumulating their visual heights. Several
 * pixel-level corrections are applied to align ports with the rendered UI:
 *
 * 1. **Base offset**: starts at `conditionNodePortItemArgsY`, which is the Y
 *    position of the first case port relative to the node top.
 *
 * 2. **Per-case accumulation**: for each preceding case with `n` expressions,
 *    add `portItemArgsY * (n + 1)` — this accounts for `n` expression rows
 *    plus one case header/separator row.
 *
 * 3. **Single-expression correction**: cases with exactly 1 expression render
 *    slightly shorter than the generic formula predicts. Subtract
 *    `singleExprCount * 7 + 2` to compensate for the reduced row height when
 *    no logical operator row is shown.
 *
 * 4. **Multi-expression correction**: cases with 2+ expressions have a compact
 *    logical operator row. Subtract `multiExprCount * 9` to offset the
 *    over-estimated spacing.
 *
 * 5. **Extra expression correction**: for cases with more than 2 expressions,
 *    each additional expression beyond the second introduces a minor spacing
 *    discrepancy. Subtract `(extraExprs + 1) * 2` to fine-tune alignment.
 *
 * @param cases - Array of case objects, each containing an `expressions` array.
 * @param caseIndex - The zero-based index of the target case whose port Y is needed.
 * @returns The Y-coordinate (in pixels) for the output port of the given case.
 */
export const getConditionNodeCasePortY = (cases: any[], caseIndex: number) => {
  let y = conditionNodePortItemArgsY;
  let singleExprCount = 0;
  let multiExprCount = 0;
  let extraExprs = 0;
  let portItemArgsYNum = 0;

  for (let i = 0; i < caseIndex; i++) {
    const notHasSub = cases[i]?.expressions?.filter((e: any) => !e?.sub_variable_condition?.conditions || e?.sub_variable_condition?.conditions.length <1).length
    const n = cases[i]?.expressions?.length || 0;
    let casePortItemArgsYNum = n + 1;
    // Add extra y for expressions with all sub_variable_condition set
    cases[i]?.expressions?.forEach((expr: any) => {
      const subs = expr?.sub_variable_condition?.conditions;
      if (subs?.length && subs.every(isSubExprSet)) {
        casePortItemArgsYNum += subs.length;
      } else if (subs?.length) {
        casePortItemArgsYNum += 1
      }
    });
    portItemArgsYNum += casePortItemArgsYNum;
    if (n === 1 && !cases[i]?.expressions?.some((e: any) => e?.sub_variable_condition?.conditions?.length > 0)) {
      singleExprCount++
    } else if (n >= 2 || cases[i]?.expressions?.some((e: any) => e?.sub_variable_condition?.conditions?.length > 0)) {
      multiExprCount++;
      cases[i]?.expressions?.forEach((e: any) => {
        const subs = e?.sub_variable_condition?.conditions;
        if (subs?.length && subs.every(isSubExprSet) && subs.length > 1) {
          extraExprs += subs.length + 2;
        } 
      });

      console.log('extraExprs notHasSub', notHasSub)
      if (notHasSub > 3) {
        extraExprs += n - 2 + notHasSub/4;
      } else {
        extraExprs += n - 2 + notHasSub/4
      }
    }
  }

  console.log('singleExprCount', singleExprCount, 'multiExprCount', multiExprCount, 'extraExprs', extraExprs)
  y += portItemArgsY * portItemArgsYNum
  // Correction for single-expression cases (slightly shorter rendered height)
  if (singleExprCount > 0) y -= singleExprCount * 7 + 2;
  // Correction for multi-expression cases (compact logical operator row)
  y -= multiExprCount * 9;
  // Correction for cases with more than 2 expressions (minor spacing drift)
  if (extraExprs > 0) y -= (extraExprs + 1) * 2;

  return y;
};
