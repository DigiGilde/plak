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
import type { MessageKey } from '../i18n/nl';

/**
 * Where, on this screen, the thing a refusal names is changed.
 *
 * The `detail` of a problem+json goes to every client of the API, the CLI
 * included, so it says what happened and not what to press. The design
 * guideline asks for both ("geef daarna feedback over wat er mis is en hoe het
 * op te lossen"), and the second half is only true here; it is added here, for
 * the codes that have such a place and for no others.
 */
const GUIDANCE: Partial<Record<string, MessageKey>> = {
  ALREADY_GROUP_MEMBER: 'error.guidance.ALREADY_GROUP_MEMBER',
  ALREADY_SITE_MEMBER: 'error.guidance.ALREADY_SITE_MEMBER',
};

/**
 * The one refusal that is about the moment rather than about the request: the
 * rate limit, whose own `detail` already says to try again later.
 */
const RATE_LIMITED = 429;

/**
 * Whether pressing the button again could land differently.
 *
 * A 4xx is the server reading this request and refusing it on what is in it:
 * the address is already a member, the role may not, the group is gone. The
 * same request gets the same answer, so offering another go promises something
 * that cannot happen. What is worth repeating is an answer about the moment: a
 * fault on the server, or no answer at all, which is every failure that never
 * became an ApiError.
 */
function worthRepeating(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  const { status } = error.problem;
  return status >= 500 || status === RATE_LIMITED;
}

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
    const again = worthRepeating(error) ? retry : '';
    if (!(error instanceof ApiError)) {
      add(text, t('admin.notice.unknownError'), again);
      return;
    }
    const guidance = GUIDANCE[error.problem.code ?? ''];
    const said = error.problem.detail ?? error.problem.title;
    add(text, guidance ? `${said} ${t(guidance)}` : said, again);
  }

  function add(text: string, detail: string, retry: string): void {
    nextId += 1;
    notices.value = [...notices.value, { id: nextId, text, detail, retry }];
  }

  function dismissNotice(id: number): void {
    notices.value = notices.value.filter((notice) => notice.id !== id);
  }

  return { notices, notify, dismissNotice };
}
