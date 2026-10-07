<script setup lang="ts">
import { computed, ref } from 'vue';

import * as plak from '@/api/plak';
import { errorText } from '@/api/client';
import { accessLabel, accessSummary, formatTimestamp, siteUrl } from '@/format';
import { useSiteGroup } from '@/composables/siteGroup';
import { t } from '@/i18n';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import UploadZone from '@/components/site/UploadZone.vue';
import SectionHeading from '@/components/SectionHeading.vue';
import CopyNotice from '@/components/CopyNotice.vue';
import { useConfirm } from '@/composables/confirm';

// The tabs have several roots (notifications beside the content); nothing
// should fall through to the markup.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  removed: [];
  changed: [];
}>();

const siteInfo = useSiteGroup().site;
const notices = ref<InstanceType<typeof Notices> | null>(null);

const uploadBusy = ref(false);
const uploadError = ref<unknown>(null);


const liveUrl = computed(() => siteUrl(props.contentBase, props.group, props.site));

/**
 * "Publieke URL" is a lie the moment anything is restricted, so the label
 * follows the access base; the line below it says who can really see the site.
 */
const addressLabel = computed(() =>
  siteInfo.value.access.base === 'public'
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
  } catch (f) {
    uploadError.value = f;
  } finally {
    uploadBusy.value = false;
  }
}

async function deleteSite(): Promise<void> {
  try {
    await plak.deleteSite(props.group, props.site);
    emit('removed');
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('site.overview.delete.failed'),
      errorText(f, t('site.overview.delete.failed.detail')),
    );
  }
}

const {
  open: deleteOpen,
  busy: deleteBusy,
  ask: askDelete,
  cancel: cancelDelete,
  confirm: confirmDelete,
} = useConfirm<true>(deleteSite);

</script>

<template>
  <Notices ref="notices" />

  <nldd-container layout="stack" gap="24">
    <section aria-labelledby="heading-status">
      <nldd-container layout="stack" gap="8">
        <SectionHeading id="heading-status" :text="t('site.overview.status.heading')" />
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

                <CopyNotice :text="copyNotice" data-testid="copy-notice" />

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
        <SectionHeading id="heading-upload" :text="t('site.overview.publish.heading')" />
        <nldd-container layout="stack" gap="16">
          <UploadZone :busy="uploadBusy" @file="publish" />
          <ErrorBanner v-if="uploadError" :error="uploadError" />
        </nldd-container>
      </nldd-container>
    </section>

    <section aria-labelledby="heading-danger-zone">
      <nldd-box background="critical">
        <nldd-container layout="stack" gap="8" padding="16">
          <SectionHeading id="heading-danger-zone" :text="t('site.overview.danger.heading')" />
          <nldd-container layout="stack" gap="16">
            <nldd-rich-text>
              <p>{{ t('site.overview.danger.body') }}</p>
            </nldd-rich-text>
            <nldd-button-group orientation="horizontal">
              <nldd-button
                variant="destructive"
                :text="t('site.overview.danger.action')"
                data-testid="delete-site"
                @click="askDelete(true)"
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
        @confirm="confirmDelete"
        @close="cancelDelete"
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
