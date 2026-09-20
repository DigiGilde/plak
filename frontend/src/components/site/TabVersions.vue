<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { Version } from '@/api/types';
import { contentUrl, formatTimestamp } from '@/format';
import { t } from '@/i18n';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import RowActions, { type RowAction } from '@/components/RowActions.vue';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  changed: [];
}>();

const loading = ref(true);
const error = ref<unknown>(null);
const versions = ref<Version[]>([]);
const busyWith = ref<string | null>(null);
const notices = ref<InstanceType<typeof Notices> | null>(null);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    versions.value = await plak.versions(props.group, props.site);
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => [props.group, props.site], load);

// History is target "live" only; preview versions live on the
// Previews tab.
const liveVersions = computed(() =>
  versions.value
    .filter((v) => v.target === 'live')
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt)),
);

function viewUrl(version: Version): string {
  return contentUrl(props.contentBase, `/${props.group}/${props.site}/_version/${version.id}/`);
}

/**
 * A new tab rather than same-tab navigation: the version lives on the content
 * host, so following it in place would drop you out of the admin interface.
 * nldd-menu-item can render an href as a real anchor, but without a target, so
 * opening happens from the handler. The "nieuw tabblad" note is there because
 * WCAG 3.2.5 asks for a warning before a window opens unannounced.
 */
function actionsFor(version: Version): RowAction[] {
  const actions: RowAction[] = [
    {
      text: t('site.versions.view'),
      icon: 'external-link',
      details: t('site.versions.view.details'),
      testid: `bekijk-${version.id}`,
      run: () => void window.open(viewUrl(version), '_blank', 'noopener'),
    },
  ];
  if (!version.isLive) {
    actions.push({
      text: t('site.versions.setLive'),
      icon: 'globe',
      disabled: busyWith.value === version.id,
      testid: `live-zetten-${version.id}`,
      run: () => void setLive(version),
    });
  }
  return actions;
}

function origin(version: Version): string {
  if (version.origin === 'upload') {
    // The name, never the id: createdByMember is a UUID and means nothing to
    // whoever reads this list.
    return version.createdByName
      ? t('site.versions.origin.uploadBy', { name: version.createdByName })
      : t('site.versions.origin.upload');
  }
  return version.createdByRepository
    ? t('site.versions.origin.repository', { repository: version.createdByRepository })
    : t('site.versions.origin.ci');
}

// Rolling back changes what visitors see: carry it out explicitly and report
// afterwards, do not assume optimistically.
async function setLive(version: Version): Promise<void> {
  busyWith.value = version.id;
  try {
    await plak.setVersionLive(props.group, props.site, version.id);
    notices.value?.notify(
      'success',
      t('site.versions.setLive.done'),
      t('site.versions.setLive.done.detail', { timestamp: formatTimestamp(version.createdAt) }),
    );
    emit('changed');
    await load();
  } catch (f) {
    notices.value?.notify(
      'critical',
      t('site.versions.setLive.failed'),
      f instanceof ApiError
        ? (f.problem.detail ?? f.problem.title)
        : t('site.versions.setLive.failed.detail'),
    );
  } finally {
    busyWith.value = null;
  }
}
</script>

<template>
  <Notices ref="notices" />

  <ErrorBanner v-if="error" :error="error" />

  <section v-else aria-labelledby="kop-versies">
    <nldd-container layout="stack" gap="8">
      <nldd-title :size="4">
        <h2 id="kop-versies">{{ t('site.versions.heading') }}</h2>
        <span slot="subtitle">{{ t('site.versions.intro') }}</span>
      </nldd-title>

      <!--
        The skeleton shows the shape of the rows to come straight away; the
        indicator around it waits 1000 ms on its own (timing="delay") and only
        then dims the skeleton behind it.
      -->
      <nldd-activity-indicator v-if="loading" :text="t('site.versions.loading')">
        <nldd-list type="form" variant="box-tinted" aria-hidden="true" data-testid="versies-skelet">
          <nldd-list-item v-for="row in 3" :key="row" size="md">
            <nldd-cell width="fit-content">
              <span class="skeleton__bar skeleton__bar--marker"></span>
            </nldd-cell>
            <nldd-spacer-cell size="12"></nldd-spacer-cell>
            <nldd-cell width="full">
              <span class="skeleton__text">
                <span class="skeleton__bar skeleton__bar--timestamp"></span>
                <span class="skeleton__bar skeleton__bar--origin"></span>
              </span>
            </nldd-cell>
            <nldd-cell width="fit-content">
              <span class="skeleton__bar skeleton__bar--acties"></span>
            </nldd-cell>
          </nldd-list-item>
        </nldd-list>
      </nldd-activity-indicator>

      <nldd-inline-dialog
        v-else-if="liveVersions.length === 0"
        icon="history"
        :text="t('site.versions.empty')"
        :supporting-text="t('site.versions.empty.hint')"
        data-testid="versies-leeg"
      ></nldd-inline-dialog>

      <nldd-list
        v-else
        type="form"
        variant="box-tinted"
        :accessible-label="t('site.versions.listLabel')"
      >
        <nldd-list-item
          v-for="version in liveVersions"
          :key="version.id"
          size="md"
          :data-testid="`versie-${version.id}`"
        >
          <!-- The marker leads the row, the way the live column does on the
               site overview. Beside the buttons it read as a third button of a
               slightly different height; here the right of the row holds
               controls and nothing else. -->
          <nldd-cell width="fit-content">
            <nldd-badge
              :color="version.isLive ? 'success' : 'neutral'"
              :accessible-label="
                version.isLive ? t('site.versions.marker.live') : t('site.versions.marker.notLive')
              "
              :data-testid="`live-marker-${version.id}`"
            ></nldd-badge>
          </nldd-cell>
          <nldd-spacer-cell size="12"></nldd-spacer-cell>
          <nldd-text-cell
            :text="formatTimestamp(version.createdAt)"
            :supporting-text="origin(version)"
          ></nldd-text-cell>
          <!-- Both actions in the row menu rather than as two buttons beside
               each other. At 200 percent text in a 320 px viewport the label
               "Zet deze versie live" alone measured 262 px and ran 151 px past
               the edge, which is a reflow failure (WCAG 1.4.10). It is also
               the shape every other list in this interface uses. -->
          <nldd-cell width="fit-content">
            <RowActions
              :label="formatTimestamp(version.createdAt)"
              :actions="actionsFor(version)"
            />
          </nldd-cell>
        </nldd-list-item>
      </nldd-list>
    </nldd-container>
  </section>
</template>

<style scoped>
/*
 * Hand-made skeleton: the design system ships no component for it. The blocks
 * stay static (no shimmer) and sit outside the accessibility tree; the
 * indicator around them carries the loading announcement.
 */
.skeleton__text {
  display: flex;
  flex-direction: column;
  gap: var(--primitives-space-6, 6px);
}

.skeleton__bar {
  display: block;
  border-radius: var(--primitives-corner-radius-xs, 4px);
  background: var(--semantics-dividers-color, #e6e8ea);
}

.skeleton__bar--timestamp {
  width: 10rem;
  height: 1.25rem;
}

.skeleton__bar--origin {
  width: 7rem;
  height: 1rem;
}

.skeleton__bar--marker {
  width: var(--primitives-space-12, 12px);
  height: var(--primitives-space-12, 12px);
  border-radius: var(--primitives-corner-radius-full, 9999px);
}

.skeleton__bar--acties {
  width: var(--primitives-space-32, 32px);
  height: var(--primitives-space-32, 32px);
  border-radius: var(--primitives-corner-radius-md, 8px);
}
</style>
