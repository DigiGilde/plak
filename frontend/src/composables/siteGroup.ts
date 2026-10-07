import { inject, type ComputedRef, type InjectionKey, type Ref } from 'vue';

import { ApiError } from '@/api/client';
import type { GroupDetail, Site } from '@/api/types';
import { t } from '@/i18n';

/**
 * The group a site page shows, fetched once by the page and handed to its
 * tabs. Tabs only render once both exist, so neither is ever null here.
 */
export interface SiteGroup {
  detail: Ref<GroupDetail>;
  site: ComputedRef<Site>;
}

export const SITE_GROUP: InjectionKey<SiteGroup> = Symbol('site-group');

export function useSiteGroup(): SiteGroup {
  const siteGroup = inject(SITE_GROUP);
  if (!siteGroup) throw new Error('useSiteGroup needs a site page around it');
  return siteGroup;
}

export function siteNotFound(group: string, site: string): ApiError {
  return new ApiError({
    type: 'about:blank',
    title: t('site.notFound.title'),
    status: 404,
    detail: t('site.notFound.detail', { site, group }),
  });
}
