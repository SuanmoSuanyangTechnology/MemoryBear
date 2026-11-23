/**
 * FAQ import result notice, rendered via antd `notification`.
 *
 * The visual stays identical to the detail-page prototype: a fixed top-right
 * toast with a 360px white card, red title/entries on failure, and a scrollable
 * failure list. Success auto-dismisses after 3s; failures persist until closed.
 * Positioning, stacking, animations and de-duplication are delegated to antd.
 */
import clsx from 'clsx';
import type { NotificationInstance } from 'antd/es/notification/interface';

export interface FaqImportNotice {
  title: string;
  summary: string;
  errors: string[];
  failed: boolean;
}

/** Shared key so a newer result replaces the previous one instead of stacking. */
export const FAQ_IMPORT_RESULT_KEY = 'faq-import-result';

const RESULT_NOTICE_CLASS = 'faq-import-result-notice';

export function showFaqImportResult(
  notification: NotificationInstance,
  notice: FaqImportNotice,
) {
  notification.open({
    key: FAQ_IMPORT_RESULT_KEY,
    duration: notice.failed ? 0 : 3,
    icon: null,
    className: RESULT_NOTICE_CLASS,
    message: (
      <span className={clsx('rb:text-[13px] rb:font-semibold', notice.failed && 'rb:text-[#c84a48]')}>
        {notice.title}
      </span>
    ),
    description: (
      <div className="rb:text-[12px]">
        <div className="rb:leading-4.75 rb:text-gray-600">{notice.summary}</div>
        {notice.errors.length > 0 && (
          <ul className="rb:max-h-40 rb:overflow-y-auto rb:list-none rb:mt-2.5! rb:p-0! rb:pt-2! rb:border-t rb:border-t-[#eef0f3]">
            {notice.errors.map((error, index) => (
              <li
                key={`${error}-${index}`}
                className="rb:py-0.75 rb:text-[#c84a48] rb:leading-4.5 rb:wrap-anywhere"
              >
                {error}
              </li>
            ))}
          </ul>
        )}
      </div>
    ),
  });
}

export function dismissFaqImportResult(notification: NotificationInstance) {
  notification.destroy(FAQ_IMPORT_RESULT_KEY);
}
