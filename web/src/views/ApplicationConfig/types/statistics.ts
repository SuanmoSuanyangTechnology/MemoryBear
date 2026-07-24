/**
 * Statistics / dashboard data types.
 */

/**
 * Statistics item data
 */
export interface StatisticsItem {
  /** Count value */
  count: number;
  /** Date string */
  date: string;
  /** Index signature for compatibility with ChartData */
  [key: string]: string | number;
}

/**
 * Statistics data structure
 */
export interface StatisticsData {
  /** Daily conversations statistics */
  daily_conversations: StatisticsItem[];
  /** Daily new users statistics */
  daily_new_users: StatisticsItem[];
  /** Daily API calls statistics */
  daily_api_calls: StatisticsItem[];
  /** Daily tokens usage statistics */
  daily_tokens: StatisticsItem[];
  /** Total conversations count */
  total_conversations: number;
  /** Total new users count */
  total_new_users: number;
  /** Total API calls count */
  total_api_calls: number;
  /** Total tokens used */
  total_tokens: number;
}
