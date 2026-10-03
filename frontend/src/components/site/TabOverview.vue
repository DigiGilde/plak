<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { Site, Version } from '@/api/types';
import { accessLabel, accessSummary, formatTimestamp, siteUrl } from '@/format';
import { t } from '@/i18n';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import UploadZone from '@/components/site/UploadZone.vue';

// The tabs have several roots (notifications beside the content); nothing
// should fall through to the markup.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  removed: [];
  changed: [];
}>();

const loading = ref(true);
const error = ref<unknown>(null);
const siteInfo = ref<Site | null>(null);
const versions = ref<Version[]>([]);
const notices = ref<InstanceType<typeof Notices> | null>(null);

const uploadBusy = ref(false);
const uploadError = ref<unknown>(null);

const deleteOpen = ref(false);
const deleteBusy = ref(false);

const liveUrl = computed(() => siteUrl(props.contentBase, props.group, props.site));

/**
 * "Publieke URL" is a lie the moment anything is restricted, so the label
 * follows the access base; the line below it says who can really see the site.
 */
const addressLabel = computed(() =>
  siteInfo.value?.access.base === 'public'
    ? t('site.overview.addressLabel.public')
    : t('site.overview.addressLabel.restricted'),
);

/** Empty until something is copied; sits beside the button, not in a toast. */
const copyNotice = ref('');

async function copyAddress(): Promise<void> {
  try {
    await navigator.clipboard.writeText(liveUrl.value);
    copyNotice.value = t('site.overview.copied');
  } catch {
    // Copying may fail (no permission, no secure context); the address is
    // visible on screen, so say so instead of staying silent.
    copyNotice.value = t('site.overview.copyFailed');
  }
}


async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [detail, versionList] = await Promise.all([
      plak.group(props.group),
      plak.versions(props.group, props.site),
    ]);
    const found = detail.sites.find((p) => p.slug === props.site);
    if (!found) {
      error.value = new ApiError({
        type: 'about:blank',
        title: t('site.notFound.title'),
        status: 404,
        detail: t('site.notFound.detail', { site: props.site, group: props.group }),
      });
      siteInfo.value = null;
      return;
    }
    siteInfo.value = found;
    versions.value = versionList;
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => [props.group, props.site], load);

// Publishing carries weight and cannot be undone: no optimistic assumption,
// but an explicit action with a confirmation afterwards.
async function publish(file: File): Promise<void> {
  uploadBusy.value = true;
  uploadError.value = null;
  try {
    await plak.upload(props.group, props.site, file, file.name);
    notices.value?.notify(
      'success',
      t('site.overview.published.title'),
      t('site.overview.published.detail'),
    );
    emit('changed');
    await load();
  } catch (f) {
    uploadError.value = f;
  } finally {
    uploadBusy.value = false;
  }
}

async function deleteSite(): Promise<void> {
  deleteBusy.value = true;
  try {
    await plak.deleteSite(props.group, props.site);
    deleteOpen.value = false;
    emit('removed');
  } catch (f) {
    // The modal sits in the top layer and renders the page below it inert; a
    // notification there would be unreachable. So close first, then notify.
    deleteOpen.value = false;
    notices.value?.notify(
      'critical',
      t('site.overview.delete.failed'),
      errorText(f, t('site.overview.delete.failed.detail')),
    );
  } finally {
    deleteBusy.value = false;
  }
}

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

</script>

<template>
  <Notices ref="notices" />

  <nldd-activity-indicator
    v-if="loading"
    :text="t('site.overview.loading')"
  ></nldd-activity-indicator>

  <ErrorBanner v-else-if="error" :error="error" />

  <nldd-container v-else-if="siteInfo" layout="stack" gap="24">
    <section aria-labelledby="heading-status">
      <nldd-container layout="stack" gap="8">
        <nldd-title :size="4"><h2 id="heading-status">{{ t('site.overview.status.heading') }}</h2></nldd-title>
        <nldd-container layout="stack" gap="16">
          <template v-if="siteInfo.hasLiveVersion">
            <!-- Both facts about the state of this site, side by side: is it
                 live, and who may look. -->
            <nldd-container layout="wrap" gap="8">
              <nldd-tag color="success" icon="globe" :text="t('site.overview.live')"></nldd-tag>
              <nldd-tag
                color="neutral"
                icon="lock-closed"
                :text="accessLabel(siteInfo.access)"
                data-testid="visibility-tag"
              ></nldd-tag>
            </nldd-container>
            <!-- The site's address is the answer to the whole journey, so it
                 stands here as a block with its own actions, not as a line. -->
            <nldd-box>
              <nldd-container layout="stack" gap="12" padding="16">
                <nldd-container layout="stack" gap="4">
                  <nldd-text size="sm" data-testid="address-label">
                    {{ addressLabel }}
                  </nldd-text>
                  <p class="address">
                    <nldd-link
                      :href="liveUrl"
                      target="_blank"
                      size="lg"
                      data-testid="public-url"
                      >{{ liveUrl }}</nldd-link
                    >
                  </p>
                </nldd-container>

                <nldd-button-group orientation="horizontal">
                  <nldd-button
                    variant="primary"
                    :text="t('site.overview.copyAddress')"
                    start-icon="copy"
                    type="button"
                    data-testid="copy-address"
                    @click="copyAddress"
                  ></nldd-button>
                  <nldd-button
                    variant="secondary"
                    :text="t('site.overview.openSite')"
                    start-icon="open-new-page"
                    :href="liveUrl"
                    target="_blank"
                    data-testid="open-site"
                  ></nldd-button>
                </nldd-button-group>

                <!-- A status line, not a toast: it belongs to these buttons
                     and has to be announced when it appears after the copy too. -->
                <nldd-text
                  size="sm"
                  role="status"
                  data-testid="copy-notice"
                  >{{ copyNotice }}</nldd-text
                >

                <nldd-text size="sm" data-testid="visible-to">
                  {{ accessSummary(siteInfo.access) }}
                </nldd-text>
                <nldd-text size="sm">
                  {{
                    t('site.overview.lastDeploy', {
                      timestamp: formatTimestamp(siteInfo.lastPublishedAt),
                    })
                  }}
                </nldd-text>
              </nldd-container>
            </nldd-box>
          </template>
          <template v-else>
            <nldd-container layout="wrap" gap="8">
              <nldd-tag color="neutral" :text="t('site.overview.noLiveVersion')"></nldd-tag>
            </nldd-container>
            <nldd-text>
              {{ t('site.overview.noLiveVersion.hint') }}
            </nldd-text>
          </template>
        </nldd-container>
      </nldd-container>
    </section>

    <section aria-labelledby="heading-upload">
      <nldd-container layout="stack" gap="8">
        <nldd-title :size="4"><h2 id="heading-upload">{{ t('site.overview.publish.heading') }}</h2></nldd-title>
        <nldd-container layout="stack" gap="16">
          <UploadZone :busy="uploadBusy" @file="publish" />
          <ErrorBanner v-if="uploadError" :error="uploadError" />
        </nldd-container>
      </nldd-container>
    </section>

    <section aria-labelledby="heading-danger-zone">
      <nldd-box background="critical">
        <nldd-container layout="stack" gap="8" padding="16">
          <nldd-title :size="4"><h2 id="heading-danger-zone">{{ t('site.overview.danger.heading') }}</h2></nldd-title>
          <nldd-container layout="stack" gap="16">
            <nldd-rich-text>
              <p>{{ t('site.overview.danger.body') }}</p>
            </nldd-rich-text>
            <nldd-button-group orientation="horizontal">
              <nldd-button
                variant="destructive"
                :text="t('site.overview.danger.action')"
                data-testid="delete-site"
                @click="deleteOpen = true"
              ></nldd-button>
            </nldd-button-group>
          </nldd-container>
        </nldd-container>
      </nldd-box>
      <ConfirmModal
        :open="deleteOpen"
        :title="t('site.overview.danger.confirm.title', { group: props.group, site: props.site })"
        :text="t('site.overview.danger.confirm.text')"
        :keep-label="t('site.overview.danger.confirm.keep')"
        :confirm-label="t('site.overview.danger.confirm.confirm')"
        :confirm-phrase="`${props.group}/${props.site}`"
        :busy="deleteBusy"
        @confirm="deleteSite"
        @close="deleteOpen = false"
      />
    </section>
  </nldd-container>
</template>

<style scoped>
/* A site address is a long, unbreakable string; without this it runs past the
   margin on a narrow screen (measured at 390 px). */
.address {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
