import type { ChatItem } from './types'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'

/** Minimal variable shape needed to substitute `{{name}}` placeholders. */
type StatementVariable = { name: string; value?: any }

/**
 * Replaces `{{name}}` placeholders in a statement with the matching variable
 * value, leaving the placeholder untouched when the value is empty / missing.
 */
export const replaceVariables = (statement: string, variables: StatementVariable[] = []): string =>
  statement.replace(/\{\{([^}]+)\}\}/g, (match, name) => {
    const v = variables.find(item => item.name === name)
    return v?.value != null && v.value !== '' ? String(v.value) : match
  })

type OpeningStatement = FeaturesConfigForm['opening_statement']

/** Whether an opening statement is enabled and carries a non-empty statement. */
export const hasOpeningStatement = (opening_statement?: OpeningStatement): boolean =>
  !!(opening_statement?.enabled && opening_statement?.statement && opening_statement.statement.trim() !== '')

interface OpeningStatementOptions {
  /** When provided, `{{name}}` placeholders are substituted from these variables. */
  variables?: StatementVariable[]
  /** Adds `created_at: Date.now()` to the message. */
  withTimestamp?: boolean
  /** Extra fields merged onto the message (e.g. `is_hidden_refresh`). */
  extra?: Partial<ChatItem>
}

/**
 * Builds the assistant opening-statement (greeting) message, or returns `null`
 * when no opening statement is configured. Centralises the construction that was
 * duplicated across the agent / workflow / conversation / trial-run chat panels.
 */
export const buildOpeningStatementMessage = (
  opening_statement?: OpeningStatement,
  options: OpeningStatementOptions = {},
): ChatItem | null => {
  if (!hasOpeningStatement(opening_statement)) return null
  const { variables, withTimestamp, extra } = options
  const statement = opening_statement!.statement as string
  return {
    role: 'assistant',
    content: variables ? replaceVariables(statement, variables) : statement,
    ...(withTimestamp ? { created_at: Date.now() } : {}),
    meta_data: {
      suggested_questions: opening_statement!.suggested_questions || [],
    },
    ...extra,
  }
}
