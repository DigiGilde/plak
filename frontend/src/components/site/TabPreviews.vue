<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { Access, AccessBase, Preview } from '@/api/types';
import { ACCESS_BASE_VALUES } from '@/api/types';
import { accessBaseLabel, accessLabel, contentUrl, formatTimestamp } from '@/format';
import { t } from '@/i18n';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import RowActions, { type RowAction, type RowChoice } from '@/components/RowActions.vue';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const loading = ref(true);
const error = ref<unknown>(null);
const previews = ref<Preview[]>([]);
const notices = ref<InstanceType<typeof Notices> | null>(null);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    previews.value = await plak.previews(props.group, props.site);
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => [props.group, props.site], load);

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

/** The API returns `url` as a content path; the link belongs on the content host. */
function previewUrl(preview: Preview): string {
  return contentUrl(props.contentBase, preview.url);
}

async function setOverride(preview: Preview, access: Access | null): Promise<void> {
  const previous = previews.value;
  previews.value = previews.value.map((p) =>
    p.ref === preview.ref ? { ...p, accessOverride: access } : p,
  );
  try {
    const updated = await plak.setPreviewAccess(props.group, props.site, preview.ref, access);
    previews.value = previews.value.map((p) => (p.ref === preview.ref ? updated : p));
  } catch (f) {
    previews.value = previous;
    notices.value?.notify(
      'critical',
      t('site.previews.accessFailed', { ref: preview.ref }),
      errorText(f, t('site.previews.accessFailed.detail')),
    );
  }
}

/** The empty option: no override, so the preview follows the site. */
const VOLGT_SITE = '';

function accessText(preview: Preview): string {
  return preview.accessOverride
    ? accessLabel(preview.accessOverride)
    : t('site.previews.sameAsSite');
}

/**
 * The base of the override, as a radio group. An override is one whole policy,
 * so picking a base here starts it with both extras off; the two entries under
 * `actionsFor` turn them on afterwards.
 */
function choiceFor(preview: Preview): RowChoice {
  return {
    label: t('site.previews.accessChoice'),
    value: preview.accessOverride?.base ?? VOLGT_SITE,
    options: [
      {
        value: VOLGT_SITE,
        text: t('site.previews.sameAsSite'),
        testid: `override-${preview.ref}-site`,
      },
      ...ACCESS_BASE_VALUES.map((w) => ({
        value: w,
        text: accessBaseLabel(w),
        testid: `override-${preview.ref}-${w}`,
      })),
    ],
    pick: (value) =>
      void setOverride(
        preview,
        value === VOLGT_SITE
          ? null
          : { base: value as AccessBase, keys: false, invitees: false },
      ),
  };
}

function actionsFor(preview: Preview): RowAction[] {
  const override = preview.accessOverride;
  return [
    // Only while this preview has an override of its own: without one there is
    // nothing here to widen, and turning an extra on would silently invent an
    // override with a base nobody chose.
    ...(override
      ? [
          {
            text: override.keys
              ? t('site.previews.keys.turnOff')
              : t('site.previews.keys.turnOn'),
            icon: 'key',
            testid: `override-${preview.ref}-sleutels`,
            run: () => void setOverride(preview, { ...override, keys: !override.keys }),
          },
          {
            text: override.invitees
              ? t('site.previews.invitees.turnOff')
              : t('site.previews.invitees.turnOn'),
            icon: 'person-2',
            testid: `override-${preview.ref}-genodigden`,
            run: () => void setOverride(preview, { ...override, invitees: !override.invitees }),
          },
        ]
      : []),
    {
      text: t('site.previews.remove'),
      icon: 'trash',
      destructive: true,
      testid: `verwijder-${preview.ref}`,
      run: () => void remove(preview),
    },
  ];
}

async function remove(preview: Preview): Promise<void> {
  const previous = previews.value;
  previews.value = previews.value.filter((p) => p.ref !== preview.ref);
  try {
    await plak.deletePreview(props.group, props.site, preview.ref);
  } catch (f) {
    previews.value = previous;
    notices.value?.notify(
      'critical',
      t('site.previews.removeFailed', { ref: preview.ref }),
      errorText(f, t('site.previews.removeFailed.detail')),
    );
  }
}
</script>

<template>
  <Notices ref="notices" />

  <nldd-activity-indicator v-if="loading" :text="t('site.previews.loading')"></nldd-activity-indicator>

  <ErrorBanner v-else-if="error" :error="error" />

  <section v-else aria-labelledby="kop-previews">
    <nldd-container layout="stack" gap="8">
      <nldd-title :size="4">
        <h2 id="kop-previews">{{ t('site.previews.heading') }}</h2>
        <span slot="subtitle">{{ t('site.previews.intro') }}</span>
      </nldd-title>

      <nldd-inline-dialog
        v-if="previews.length === 0"
        icon="git-pull-request"
        :text="t('site.previews.empty')"
        :supporting-text="t('site.previews.empty.hint')"
        data-testid="previews-leeg"
      ></nldd-inline-dialog>

      <nldd-list
        v-else
        type="form"
        variant="box-base"
        :accessible-label="t('site.previews.listLabel')"
      >
        <nldd-list-item
          v-for="preview in previews"
          :key="preview.ref"
          size="md"
          :data-testid="`preview-${preview.ref}`"
        >
          <nldd-icon-cell icon="git-pull-request" size="20"></nldd-icon-cell>
          <nldd-title-cell
            :text="preview.ref"
            :size="5"
            min-width="120px"
            :overline="t('site.previews.accessOverline', { access: accessText(preview) })"
          >
            <nldd-link slot="supporting-text" :href="previewUrl(preview)" target="_blank">{{
              previewUrl(preview)
            }}</nldd-link>
          </nldd-title-cell>
          <!-- The URL wraps over two lines in a narrow window and would
               otherwise touch the dates beside it. -->
          <nldd-spacer-cell size="16"></nldd-spacer-cell>
          <nldd-text-cell
            width="fit-content"
            :text="
              t('site.previews.updated', { timestamp: formatTimestamp(preview.lastUpdatedAt) })
            "
            :supporting-text="
              t('site.previews.expires', { timestamp: formatTimestamp(preview.expiresAt) })
            "
          ></nldd-text-cell>
          <!-- A dropdown of 220 px plus a labelled button measured 571 px on a
               320 px screen at 200 percent text; the row does not wrap, so both
               ran off the edge (WCAG 1.4.10). The setting now lives in the row
               menu as a radio group, with its current value as the overline. -->
          <nldd-cell width="fit-content">
            <RowActions
              :label="preview.ref"
              :choice="choiceFor(preview)"
              :actions="actionsFor(preview)"
            />
          </nldd-cell>
        </nldd-list-item>
      </nldd-list>
    </nldd-container>
  </section>
</template>
