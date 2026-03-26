/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-24 15:07:49 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-24 15:07:49 
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
export const calcConditionNodeTotalHeight = (cases: any[]) => {
  // Total number of expressions across all cases
  const exprCount = cases.reduce((acc: number, c: any) => acc + (c?.expressions?.length || 0), 0);
  // Sum of expression counts only for cases that have more than one expression
  const hasMultiExprCount = cases.reduce((acc: number, c: any) => acc + (c?.expressions?.length > 1 ? c?.expressions?.length : 0), 0);

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

  for (let i = 0; i < caseIndex; i++) {
    const n = cases[i]?.expressions?.length || 0;
    y += portItemArgsY * (n + 1);
    if (n === 1) singleExprCount++;
    else if (n >= 2) {
      multiExprCount++;
      if (n > 2) extraExprs += n - 2;
    }
  }

  // Correction for single-expression cases (slightly shorter rendered height)
  if (singleExprCount > 0) y -= singleExprCount * 7 + 2;
  // Correction for multi-expression cases (compact logical operator row)
  y -= multiExprCount * 9;
  // Correction for cases with more than 2 expressions (minor spacing drift)
  if (extraExprs > 0) y -= (extraExprs + 1) * 2;

  return y;
};
