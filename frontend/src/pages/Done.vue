<script setup lang="ts">
/**
 * Result screen after "Zet een site online" (route `/{group}/{site}/done`).
 * Its own route, not a sheet over the site page: your site's address is the
 * answer to the task, and you have to be able to reload and forward it.
 *
 * The confirmation lives in the page, not in a toast: a toast does not survive
 * a remount, and here the confirmation is the entire point.
 *
 * `adressen` is deliberately a list of one: as soon as a site can have more
 * than one address, the second address joins as an extra row, with the same
 * buttons.
 */
import { computed, onMounted, ref, watch, watchEffect } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { Me, Site } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import { fetchCurrentMember } from '@/composables/currentMember';
import { setBreadcrumbs } from '@/composables/breadcrumbs';
import { takePublishedMark } from '@/composables/publishedMark';
import { groupPath } from '@/composables/slug';
import SecretLink from '@/components/site/SecretLink.vue';
import { accessSummary, formatTimestamp, siteUrl } from '@/format';
import { t } from '@/i18n';

interface Address {
  id: string;
  label: string;
  url: string;
}

const route = useRoute();
const router = useRouter();
const groupSlug = computed(() => String(route.params.group ?? ''));
const siteSlug = computed(() => String(route.params.site ?? ''));

const loading = ref(true);
const error = ref<unknown>(null);
const site = ref<Site | null>(null);
const groupName = ref('');
const contentBase = ref('');
/** The latest copy message per address; empty until something is copied. */
const copyNotice = ref<Record<string, string>>({});

/** The plaintext of the key just made; it exists only on this screen. */
const keyValue = ref<string | null>(null);
const keyError = ref<unknown>(null);
// Synchronous flag: guards a concurrent second load() pass from firing a
// second POST, on top of the "an active key already exists" check below,
// which is what actually protects against re-creating on navigation back.
let keyRequested = false;

const sitePath = computed(() => `/${groupSlug.value}/${siteSlug.value}`);

const addresses = computed<Address[]>(() => [
  {
    id: 'site',
    label: t('page.done.address.site'),
    url: siteUrl(contentBase.value, groupSlug.value, siteSlug.value),
  },
]);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  keyValue.value = null;
  keyError.value = null;
  keyRequested = false;
  const fromPublish = takePublishedMark(router);
  try {
    const [detail, loggedIn] = await Promise.all([plak.group(groupSlug.value), fetchCurrentMember()]);
    // currentMember stores the /me response typed as Member; contentBaseUrl rides along.
    contentBase.value = (loggedIn as Me | null)?.contentBaseUrl ?? '';
    groupName.value = detail.group.name;
    const found = detail.sites.find((p) => p.slug === siteSlug.value);
    if (!found) {
      error.value = new ApiError({
        type: 'about:blank',
        title: t('page.done.error.unknownSite.title'),
        status: 404,
        detail: t('page.done.error.unknownSite.detail', {
          site: siteSlug.value,
          group: groupSlug.value,
        }),
      });
      site.value = null;
      return;
    }
    site.value = found;
    if (found.access.keys && fromPublish) {
      // Not awaited: the address on this screen does not depend on it, and
      // it should not hold up the loading indicator.
      void ensureKeyLink();
    }
  } catch (e) {
    error.value = e;
  } finally {
    loading.value = false;
  }
}

/**
 * The link comes into being on the same screen as the choice. Without this,
 * publishing a site whose only way in is the secret link leaves it online but
 * unreachable: no key exists yet, and this is the only screen that can still
 * show its value (TabAccess shows it too, but only for keys created there).
 * Only on the visit the publish flow leads here (publishedMark.ts), so a link
 * to this route cannot re-create a key an admin has just revoked.
 */
async function ensureKeyLink(): Promise<void> {
  /* v8 ignore start -- a narrow race guard (two overlapping load() passes
     both reaching here before either's fetch resolves); the "active key
     already exists" check below is the one that protects the ordinary case
     of navigating back to this screen. */
  if (keyRequested) return;
  /* v8 ignore stop */
  keyRequested = true;
  try {
    const existing = await plak.keys(groupSlug.value, siteSlug.value);
    // A key already exists (e.g. navigating back to this screen): its value
    // was shown once already and cannot be shown again, so do not make another.
    if (existing.some((k) => k.status === 'active')) return;
    const result = await plak.createKey(groupSlug.value, siteSlug.value, null, null);
    keyValue.value = result.value;
  } catch (f) {
    keyError.value = f;
  }
}

onMounted(load);
watch(() => [groupSlug.value, siteSlug.value], load);

watchEffect(() => {
  setBreadcrumbs(route.path, [
    { text: t('nav.overview'), href: '/' },
    { text: groupName.value || groupSlug.value, href: groupPath(groupSlug.value) },
    { text: site.value?.title ?? siteSlug.value },
  ]);
});

async function copy(address: Address): Promise<void> {
  try {
    await navigator.clipboard.writeText(address.url);
    copyNotice.value = { ...copyNotice.value, [address.id]: t('page.done.copy.ok') };
  } catch {
    // Copying may fail (no permission, no secure context); the link itself is
    // still selectable then, so say so instead of staying silent.
    copyNotice.value = {
      ...copyNotice.value,
      [address.id]: t('page.done.copy.failed'),
    };
  }
}

function keyErrorText(): string {
  return keyError.value instanceof ApiError
    ? (keyError.value.problem.detail ?? keyError.value.problem.title)
    : t('page.done.key.failed.fallback');
}
</script>

<template>
  <nldd-simple-section>
    <nldd-inline-dialog
      v-if="loading"
      variant="loading"
      :text="t('page.done.loading')"
    ></nldd-inline-dialog>

    <ErrorBanner v-else-if="error" :error="error" />

    <template v-else-if="site">
      <nldd-title :size="1">
        <h1>
          {{ site.hasLiveVersion ? t('page.done.heading.online') : t('page.done.heading.offline') }}
        </h1>
      </nldd-title>

      <nldd-spacer size="24"></nldd-spacer>

      <nldd-container layout="stack" gap="32">
        <template v-if="site.hasLiveVersion">
          <nldd-container
            v-for="address in addresses"
            :key="address.id"
            layout="stack"
            gap="12"
            data-testid="klaar-adres"
          >
            <nldd-container layout="stack" gap="4">
              <nldd-text size="sm" color="secondary">{{ address.label }}</nldd-text>
              <p class="address">
                <nldd-link
                  :href="address.url"
                  target="_blank"
                  size="lg"
                  :data-testid="`klaar-adres-link-${address.id}`"
                  >{{ address.url }}</nldd-link
                >
              </p>
            </nldd-container>

            <nldd-button-group orientation="horizontal">
              <nldd-button
                variant="primary"
                :text="t('page.done.copy.button')"
                start-icon="copy"
                type="button"
                :data-testid="`klaar-kopieer-${address.id}`"
                @click="copy(address)"
              ></nldd-button>
              <nldd-button
                variant="secondary"
                :text="t('page.done.open')"
                start-icon="open-new-page"
                :href="address.url"
                target="_blank"
                :data-testid="`klaar-open-${address.id}`"
              ></nldd-button>
            </nldd-button-group>

            <!-- A status line, not a toast: it belongs to these buttons and
                 has to be announced when it appears after the copy too. -->
            <nldd-text
              size="sm"
              color="secondary"
              role="status"
              :data-testid="`klaar-kopieermelding-${address.id}`"
              >{{ copyNotice[address.id] ?? '' }}</nldd-text
            >
          </nldd-container>

          <nldd-banner
            variant="success"
            :text="t('page.done.published.title', { title: site.title })"
            :supporting-text="
              t('page.done.published.detail', { time: formatTimestamp(site.lastPublishedAt) })
            "
            data-testid="klaar-bevestiging"
          ></nldd-banner>
        </template>

        <nldd-inline-dialog
          v-else
          icon="globe"
          :text="t('page.done.noVersion.title')"
          :supporting-text="t('page.done.noVersion.detail')"
          horizontal-alignment="left"
          data-testid="klaar-geen-versie"
        >
          <nldd-button
            slot="actions"
            variant="primary"
            :text="t('page.done.noVersion.action')"
            :href="sitePath"
          ></nldd-button>
        </nldd-inline-dialog>

        <nldd-container layout="stack" gap="8">
          <nldd-title :size="5"><h2>{{ t('page.done.visibility.heading') }}</h2></nldd-title>
          <nldd-text data-testid="klaar-zichtbaarheid">
            {{ accessSummary(site.access) }}
          </nldd-text>
          <nldd-link :href="`${sitePath}/access`" size="md" data-testid="klaar-zichtbaarheid-wijzigen"
            >{{ t('page.done.visibility.change') }}</nldd-link
          >
        </nldd-container>

        <nldd-container v-if="site.access.keys" layout="stack" gap="4">
          <nldd-banner
            v-if="keyValue"
            variant="success"
            :text="t('page.done.key.created.title')"
            :supporting-text="t('page.done.key.created.detail')"
            data-testid="klaar-sleutel"
          >
            <SecretLink
              :value="keyValue"
              :site-url="siteUrl(contentBase, groupSlug, siteSlug)"
              prefix="klaar-sleutel"
            />
          </nldd-banner>
          <nldd-banner
            v-else-if="keyError"
            variant="critical"
            :text="t('page.done.key.failed.title')"
            :supporting-text="keyErrorText()"
            data-testid="klaar-sleutel-fout"
          >
            <nldd-button
              slot="actions"
              variant="secondary"
              :text="t('page.done.key.failed.action')"
              :href="`${sitePath}/access`"
              data-testid="klaar-sleutel-naar-toegang"
            ></nldd-button>
          </nldd-banner>
        </nldd-container>

        <nldd-container layout="stack" gap="8">
          <nldd-title :size="5"><h2>{{ t('page.done.next.heading') }}</h2></nldd-title>
          <nldd-link :href="sitePath" size="md" data-testid="klaar-naar-site"
            >{{ t('page.done.next.site') }}</nldd-link
          >
          <nldd-link href="/" size="md" data-testid="klaar-naar-overzicht"
            >{{ t('page.done.next.overview') }}</nldd-link
          >
        </nldd-container>
      </nldd-container>
    </template>
  </nldd-simple-section>
</template>

<style scoped>
/* A site address is a long, unbreakable string; without this it runs past the
   margin on a narrow screen (measured at 390 px). */
.address {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
