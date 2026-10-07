<script setup lang="ts">
import { computed, ref } from 'vue';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { Me, SiteStorage, Version } from '@/api/types';
import { contentUrl, formatTimestamp } from '@/format';
import { t } from '@/i18n';
import { fetchCurrentMember } from '@/composables/currentMember';
import { useLoader } from '@/composables/loader';
import { isSiteAdmin } from '@/composables/roles';
import { formatSize } from '@/components/site/packing';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import RowActions, { type RowAction } from '@/components/RowActions.vue';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  changed: [];
}>();

const versions = ref<Version[]>([]);
const storage = ref<SiteStorage | null>(null);
const busyWith = ref<string | null>(null);
const notices = ref<InstanceType<typeof Notices> | null>(null);
const canEditRetention = ref(false);
/** The radio choice: it can run ahead of the saved value while a number is being typed. */
const retentionOwn = ref(false);
const retentionInput = ref('');
/** What is wrong with the typed number, shown at the field; null when nothing is. */
const retentionError = ref<string | null>(null);
/** Why the last choice of an option was not saved, shown under the options. */
const choiceError = ref<string | null>(null);

const { loading, error, reload } = useLoader(
  async () => {
    // The usage line is an extra: when it cannot be loaded it is left out
    // and the list carries on.
    const [list, usage, member] = await Promise.allSettled([
      plak.versions(props.group, props.site),
      plak.siteStorage(props.group, props.site),
      fetchCurrentMember(),
    ]);
    if (list.status === 'rejected') throw list.reason;
    return { list: list.value, usage, member };
  },
  ({ list, usage, member }) => {
    versions.value = list;
    storage.value = usage.status === 'fulfilled' ? usage.value : null;
    canEditRetention.value =
      member.status === 'fulfilled' && isSiteAdmin(member.value as Me | null, props.group, props.site);
    if (storage.value) {
      retentionOwn.value = !storage.value.liveVersionsKeptIsDefault;
      retentionInput.value = String(storage.value.liveVersionsKept);
      retentionError.value = null;
      choiceError.value = null;
    }
  },
  () => [props.group, props.site],
);

function retentionRule(kept: number): string {
  if (kept === 0) return t('site.versions.retention.all');
  if (kept === 1) return t('site.versions.retention.one');
  return t('site.versions.retention.many', { kept: String(kept) });
}

const storageText = computed(() => {
  const s = storage.value;
  if (!s) return '';
  const used = formatSize(s.usedBytes);
  const usage =
    s.maxBytes > 0
      ? t('site.versions.storage.quota', { used, max: formatSize(s.maxBytes) })
      : t('site.versions.storage.noQuota', { used });
  const own = s.liveVersionsKeptIsDefault ? '' : ` ${t('site.versions.retention.own')}`;
  return `${usage} ${retentionRule(s.liveVersionsKept)}${own}`;
});

function defaultRetentionLabel(current: SiteStorage): string {
  const kept = current.defaultLiveVersionsKept;
  if (kept === 0) return t('site.versions.keep.default.all');
  if (kept === 1) return t('site.versions.keep.default.one');
  return t('site.versions.keep.default', { count: String(kept) });
}

function inputValue(event: Event): string {
  return (
    (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLInputElement).value
  );
}

/** The refusals that are about the typed number, and belong at the field. */
const FIELD_REFUSALS = new Set(['LIVE_VERSIONS_KEPT_INVALID', 'LIVE_VERSIONS_KEPT_TOO_LARGE']);

function failureText(f: unknown): string {
  if (!(f instanceof ApiError)) return t('site.versions.keep.failed.network');
  const reason = f.problem.detail ?? f.problem.title;
  if (f.problem.status === 422 && FIELD_REFUSALS.has(f.problem.code ?? '')) return reason;
  // The sentence goes on after the reason, which usually ends in a full stop.
  return t('site.versions.keep.failed.problem', { reason: reason.replace(/\.$/, '') });
}

/**
 * A failed save takes nothing back: the option and the number stay as they
 * were left, the reason is shown beside what was tried, and trying again is
 * choosing or committing once more. `storage` only ever holds what was saved,
 * so the sentence above the box keeps telling the truth.
 */
async function saveRetention(
  current: SiteStorage,
  next: number | null,
  from: 'choice' | 'field',
): Promise<void> {
  try {
    const updated = await plak.setLiveVersionsKept(props.group, props.site, next);
    const own = updated.liveVersionsKept;
    storage.value = {
      ...current,
      liveVersionsKept: own ?? current.defaultLiveVersionsKept,
      liveVersionsKeptIsDefault: own === null,
    };
    retentionOwn.value = own !== null;
    retentionInput.value = String(storage.value.liveVersionsKept);
    retentionError.value = null;
    choiceError.value = null;
    notices.value?.notify(
      'success',
      t('site.versions.keep.saved'),
      retentionRule(storage.value.liveVersionsKept),
    );
  } catch (f) {
    if (from === 'field') {
      retentionError.value = failureText(f);
    } else {
      choiceError.value = failureText(f);
    }
  }
}

function chooseRetentionDefault(current: SiteStorage): void {
  retentionError.value = null;
  choiceError.value = null;
  retentionOwn.value = false;
  if (!current.liveVersionsKeptIsDefault) {
    void saveRetention(current, null, 'choice');
  }
}

/** Choosing a number of its own starts from the number in force, and saves it. */
function chooseRetentionOwn(current: SiteStorage): void {
  choiceError.value = null;
  retentionOwn.value = true;
  if (current.liveVersionsKeptIsDefault) {
    retentionInput.value = String(current.liveVersionsKept);
    void saveRetention(current, current.liveVersionsKept, 'choice');
  }
}

/**
 * A row that is already checked fires no change when it is chosen again, so
 * trying a failed choice again comes in as a click on that row.
 */
function retryChoice(current: SiteStorage, own: boolean): void {
  if (choiceError.value === null || retentionOwn.value !== own) return;
  if (own) {
    chooseRetentionOwn(current);
  } else {
    chooseRetentionDefault(current);
  }
}

/** Editing the number takes back what was said about the previous one. */
function editRetention(event: Event): void {
  retentionInput.value = inputValue(event);
  retentionError.value = null;
}

/** On change (leaving the field or Enter), never per keystroke. */
function commitRetention(current: SiteStorage, event: Event): void {
  retentionInput.value = inputValue(event);
  const text = retentionInput.value.trim();
  if (!/^\d+$/.test(text)) {
    retentionError.value = t('site.versions.keep.count.invalid');
    return;
  }
  retentionError.value = null;
  if (current.liveVersionsKeptIsDefault || Number(text) !== current.liveVersionsKept) {
    void saveRetention(current, Number(text), 'field');
  }
}

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
      testid: `view-${version.id}`,
      run: () => void window.open(viewUrl(version), '_blank', 'noopener'),
    },
  ];
  if (!version.isLive) {
    actions.push({
      text: t('site.versions.setLive'),
      icon: 'globe',
      disabled: busyWith.value === version.id,
      testid: `set-live-${version.id}`,
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
    await reload();
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

  <section v-else aria-labelledby="heading-versions">
    <nldd-container layout="stack" gap="8">
      <nldd-title :size="4">
        <h2 id="heading-versions">{{ t('site.versions.heading') }}</h2>
        <span slot="subtitle">{{ t('site.versions.intro') }}</span>
      </nldd-title>

      <nldd-rich-text v-if="!loading && storageText" data-testid="versions-storage">
        <p>{{ storageText }}</p>
      </nldd-rich-text>

      <!--
        The choice and the number it opens share one box. The number cannot sit
        in the radiogroup itself: that list runs a roving tab stop and would
        hold a text field out of the tab order. A form list around both keeps
        Tab going from the choice straight to the field.
      -->
      <nldd-list
        v-if="!loading && storage && canEditRetention"
        type="form"
        variant="box-tinted"
        dividers="never"
        data-testid="retention-box"
      >
        <nldd-list-item size="md">
          <nldd-cell width="full">
            <nldd-list
              type="radiogroup"
              variant="simple"
              dividers="never"
              :accessible-label="t('site.versions.keep.label')"
              data-testid="retention-choice"
            >
              <nldd-list-item
                radio
                size="md"
                :checked="!retentionOwn || undefined"
                data-testid="retention-default"
                @change="chooseRetentionDefault(storage)"
                @click="retryChoice(storage, false)"
              >
                <nldd-cell width="fit-content">
                  <nldd-radio-button decorative :checked="!retentionOwn || undefined"></nldd-radio-button>
                </nldd-cell>
                <nldd-spacer-cell size="12"></nldd-spacer-cell>
                <nldd-title-cell
                  :size="6"
                  :text="defaultRetentionLabel(storage)"
                  :supporting-text="t('site.versions.keep.default.hint')"
                ></nldd-title-cell>
              </nldd-list-item>
              <nldd-list-item
                radio
                size="md"
                :checked="retentionOwn || undefined"
                data-testid="retention-custom"
                @change="chooseRetentionOwn(storage)"
                @click="retryChoice(storage, true)"
              >
                <nldd-cell width="fit-content">
                  <nldd-radio-button decorative :checked="retentionOwn || undefined"></nldd-radio-button>
                </nldd-cell>
                <nldd-spacer-cell size="12"></nldd-spacer-cell>
                <nldd-title-cell
                  :size="6"
                  :text="t('site.versions.keep.own')"
                  :supporting-text="t('site.versions.keep.own.hint')"
                ></nldd-title-cell>
              </nldd-list-item>
            </nldd-list>
      <div v-if="!loading && storage && canEditRetention && choiceError" role="alert">
        <nldd-inline-dialog
          variant="alert"
          horizontal-alignment="left"
          :text="choiceError"
          data-testid="retention-choice-error"
        ></nldd-inline-dialog>
      </div>
          </nldd-cell>
        </nldd-list-item>
        <!-- Indented by the width of the radio (24) and the spacer after it (12). -->
        <nldd-list-item v-if="retentionOwn" size="md" data-testid="retention-count-row">
          <nldd-spacer-cell size="24"></nldd-spacer-cell>
          <nldd-spacer-cell size="12"></nldd-spacer-cell>
          <nldd-cell width="full">
            <nldd-form-field class="retention-field" :label="t('site.versions.keep.count.label')">
              <nldd-text-field
                name="retention-count"
                keyboard="numeric"
                width="8rem"
                autocomplete="off"
                :value="retentionInput"
                :invalid="retentionError !== null || undefined"
                :unmet="retentionError !== null ? 'retention-invalid' : undefined"
                data-testid="retention-count"
                @input="editRetention($event)"
                @change="commitRetention(storage, $event)"
              ></nldd-text-field>
              <nldd-form-field-help-text>
                {{ t('site.versions.keep.count.help') }}
              </nldd-form-field-help-text>
              <nldd-validation-list>
                <nldd-validation-item id="retention-invalid">
                  {{ retentionError }}
                </nldd-validation-item>
              </nldd-validation-list>
            </nldd-form-field>
          </nldd-cell>
        </nldd-list-item>
      </nldd-list>

      <!--
        The skeleton shows the shape of the rows to come straight away; the
        indicator around it waits 1000 ms on its own (timing="delay") and only
        then dims the skeleton behind it.
      -->
      <nldd-activity-indicator v-if="loading" :text="t('site.versions.loading')">
        <nldd-list type="form" variant="box-tinted" aria-hidden="true" data-testid="versions-skeleton">
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
              <span class="skeleton__bar skeleton__bar--actions"></span>
            </nldd-cell>
          </nldd-list-item>
        </nldd-list>
      </nldd-activity-indicator>

      <nldd-inline-dialog
        v-else-if="liveVersions.length === 0"
        icon="history"
        :text="t('site.versions.empty')"
        :supporting-text="t('site.versions.empty.hint')"
        data-testid="versions-empty"
      ></nldd-inline-dialog>

      <nldd-list
        v-else
        type="form"
        variant="box-tinted"
        :accessible-label="t('site.versions.listLabel')"
        data-testid="versions-list"
      >
        <nldd-list-item
          v-for="version in liveVersions"
          :key="version.id"
          size="md"
          :data-testid="`version-${version.id}`"
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
 * nldd-cell lines its content up at the start and lets it shrink to its
 * minimum, which folds a form field to one word per line; the field takes the
 * width of the cell instead. It also moves up past the bottom padding of the
 * row around the options and the top padding of its own row (two list-item
 * paddings, which no size attribute removes), so it reads as part of the
 * custom option while the box keeps its normal padding.
 */
.retention-field {
  align-self: stretch;
  margin-block-start: calc(-1 * var(--primitives-space-16));
}

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

.skeleton__bar--actions {
  width: var(--primitives-space-32, 32px);
  height: var(--primitives-space-32, 32px);
  border-radius: var(--primitives-corner-radius-md, 8px);
}
</style>
