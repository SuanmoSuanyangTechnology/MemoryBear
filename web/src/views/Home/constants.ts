export const activityKinds = ['extraction', 'forgetting', 'reflection', 'retrieval', 'other'] as const

export const ACTIVITY_START = '2026-08-01'
// Proposed contract; keep mock mode explicit until the backend endpoint is available.
export const HOME_ACTIVITY_MOCK = false


// Reuse the dashboard's semantic palette for both chart modes.
export const activityColors = ['#155EEF', '#FFA940', '#8D4EDA', '#91CEFF', '#DFE4ED']