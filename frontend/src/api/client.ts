import { currentLocale, t } from '../i18n';

const CSRF_COOKIE = '__Host-plak-csrf';
const CSRF_HEADER = 'X-CSRF-Token';
const MUTATION_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/**
 * The language the API answers a refusal in. The API itself defaults to
 * English; this asks for whatever the interface is in right now, so the
 * `title` and `detail` of a problem+json arrive in the language on screen
 * instead of in the one the interface happened to be built for.
 */
const ACCEPT_LANGUAGE_HEADER = 'Accept-Language';

/** RFC 9457 problem+json error object, as the backend returns it. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  /**
   * Machine-readable reason, stable across rewordings of `detail` and across
   * an interface in another language. Absent on errors the framework raises
   * itself rather than the application.
   */
  code?: string;
}

export class ApiError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.title);
    this.name = 'ApiError';
    this.problem = problem;
  }
}

function readCookie(name: string): string | null {
  const row = document.cookie.split('; ').find((part) => part.startsWith(`${name}=`));
  return row ? decodeURIComponent(row.slice(name.length + 1)) : null;
}

async function parseError(response: Response): Promise<Problem> {
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/problem+json')) {
    const body = (await response.json()) as Partial<Problem>;
    return {
      type: body.type ?? 'about:blank',
      title: body.title ?? response.statusText,
      status: body.status ?? response.status,
      detail: body.detail,
      // The machine-readable reason. This object is rebuilt field by field, so
      // a field left out here is invisible to the whole SPA.
      code: body.code,
    };
  }
  return {
    type: 'about:blank',
    title: response.statusText || t('error.generic.title'),
    status: response.status,
  };
}

export type RequestOptions = RequestInit;

/**
 * Fetch wrapper for the admin API: same-origin, sets the CSRF header on
 * mutating requests (double submit against the __Host cookie) and throws an
 * ApiError carrying a typed Problem object on any non-2xx response.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  const headers = new Headers(options.headers);

  headers.set(ACCEPT_LANGUAGE_HEADER, currentLocale.value);

  if (MUTATION_METHODS.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf !== null) {
      headers.set(CSRF_HEADER, csrf);
    }
  }

  const response = await fetch(path, {
    ...options,
    method,
    headers,
    credentials: 'same-origin',
  });

  if (!response.ok) {
    throw new ApiError(await parseError(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
