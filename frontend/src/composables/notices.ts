/**
 * Error notifications for optimistically updated actions: the UI shows the
 * result straight away and rolls back on failure, after which this message
 * tells the user what went wrong (NLDD guideline "werk de interface
 * optimistisch bij"). One list per component; the `nldd-notification` elements
 * find their own spot on screen and stack up there together.
 */
import { ref } from 'vue';

import { ApiError } from '../api/client';
import { t } from '../i18n';

export interface Notice {
  id: number;
  text: string;
  detail: string;
  /** Input to retry the action with; empty when there is nothing to retry. */
  retry: string;
}

export function useNotices() {
  const notices = ref<Notice[]>([]);
  let nextId = 0;

  function notify(text: string, error: unknown, retry = ''): void {
    const detail =
      error instanceof ApiError
        ? (error.problem.detail ?? error.problem.title)
        : t('admin.notice.unknownError');
    nextId += 1;
    notices.value = [...notices.value, { id: nextId, text, detail, retry }];
  }

  function dismissNotice(id: number): void {
    notices.value = notices.value.filter((notice) => notice.id !== id);
  }

  return { notices, notify, dismissNotice };
}
