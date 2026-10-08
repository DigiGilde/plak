<script setup lang="ts">
/**
 * The address section of the settings tab of a group and of a site: where it
 * is now, which old addresses still redirect, and for whoever may change it
 * the form that does, in a sheet they open for it.
 */
import { computed, nextTick, ref, watch } from 'vue';

import { ApiError } from '@/api/client';
import * as plak from '@/api/plak';
import type { Group, PreviousSlug, Site } from '@/api/types';
import CodeSentence from '@/components/CodeSentence.vue';
import ConfirmModal from '@/components/ConfirmModal.vue';
import Notices from '@/components/site/Notices.vue';
import { SLUG_MATCH, SLUG_PATTERN, SLUG_RE, slugifyTyped } from '@/composables/slug';
import { lastRedirectDay, redirectDayFromToday, siteUrl } from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  kind: 'group' | 'site';
  /** The slug of the group; of the group a site sits in, for a site. */
  group: string;
  /** The slug of the site, for the address of a site. */
  site?: string;
  previousSlugs: PreviousSlug[];
  contentBase: string;
  /** How many days an old address keeps redirecting, from `/me`. */
  redirectDays: number;
  canChange: boolean;
  /** Admin of this site, but without a role in its group: told why that is not enough. */
  siteOnlyAdmin?: boolean;
  /** Whether the address, or an address in the group, may have been shared with the world. */
  isPublic: boolean;
  /** The sites of a group, to count them and to give an example. */
  sites?: Site[];
  /** The id a workflow names, for a site. */
  siteId?: string;
  /** The other sites in the group, for a site: an address one gave up still redirects there. */
  siblings?: Site[];
  /** Whether an address another site gave up may be taken from it: a group admin. */
  canRelease?: boolean;
}>();

const emit = defineEmits<{
  renamed: [Group | Site];
}>();

/** A sentence of the catalogue, and the code that goes where its placeholders are. */
interface Sentence {
  text: string;
  codes: Record<string, string>;
  links?: Record<string, string>;
}

/** The slug that is being changed: the site's, or the group's when there is no site. */
const current = computed(() => props.site ?? props.group);

const editing = ref(false);
const typed = ref('');
const pending = ref('');
const open = ref(false);
const busy = ref(false);
/** What is wrong with the address, as a refusal by the server told it. */
const verdict = ref<string | null>(null);
const notice = ref<Sentence | null>(null);
const field = ref<HTMLElement | null>(null);
const startButton = ref<HTMLElement | null>(null);
const list = ref<(HTMLElement & { judging: boolean }) | null>(null);
const sheet = ref<(HTMLElement & { show?: () => void; hide?: () => void }) | null>(null);
const notices = ref<InstanceType<typeof Notices> | null>(null);

const testPrefix = computed(() => `${props.kind}-address`);

// -- How an address reads --------------------------------------------------

/** On the page: a site as the URL it is served at, a group as its slug. */
function shown(slug: string): string {
  return props.kind === 'site' ? siteUrl(props.contentBase, props.group, slug) : slug;
}

/** The short form, for the title of the dialog and the status line, which are read out. */
function short(slug: string): string {
  return props.kind === 'site' ? `${props.group}/${slug}` : slug;
}

const typedIsAddress = computed(() => SLUG_RE.test(typed.value));

/** What a change comes to is said only of an address it can be changed to. */
const typedIsNew = computed(() => typedIsAddress.value && typed.value !== current.value);

/**
 * Whether the field has been judged: left with something in it, or submitted.
 * From then on its mark follows the value, as nldd-form does after a submit.
 */
const judged = ref(false);
const ruleFails = computed(() => judged.value && !typedIsNew.value);
/** The rule the value broke when the field was left, said aloud once. */
const leftVerdict = ref<string | null>(null);

/** The help text under the field: the address the typed slug makes. */
const preview = computed(() =>
  props.kind === 'site' ? siteUrl(props.contentBase, props.group, typed.value) : typed.value,
);

/** Read again when a change is asked for: the page can stay open past midnight. */
const today = ref(new Date());
const untilDate = computed(() => redirectDayFromToday(props.redirectDays, today.value));

// -- What is said about the address now ---------------------------------------

const example = computed(() => props.sites?.[0]);
const siteCount = computed(() => props.sites?.length ?? 0);

const currentSentence = computed((): Sentence => {
  if (props.kind === 'site') {
    const address = shown(current.value);
    return { text: t('address.current.site'), codes: { address }, links: { address } };
  }
  const first = example.value;
  if (!first) return { text: t('address.current.group.empty'), codes: { slug: props.group } };
  const url = siteUrl(props.contentBase, props.group, first.slug);
  return {
    text: t('address.current.group'),
    codes: { slug: props.group, example: url },
    links: { example: url },
  };
});

const readOnlyText = computed(() => {
  if (props.kind === 'group') return t('address.readOnly.group');
  return props.siteOnlyAdmin ? t('address.readOnly.siteOnly') : t('address.readOnly.site');
});

// -- What a change comes to ------------------------------------------------------

const workflow = computed((): Sentence => {
  const codes = { site: 'site:', flag: '--site', command: 'plak publish' };
  if (props.kind === 'site') {
    return {
      text: t('address.consequences.workflow.site'),
      codes: {
        ...codes,
        address: `${props.group}/${typed.value}`,
        siteId: `site-id: ${props.siteId}`,
      },
    };
  }
  return {
    text: t('address.consequences.workflow.group'),
    codes: { ...codes, address: `${typed.value}/${t('address.placeholder.site')}` },
  };
});

const sitesSentence = computed((): Sentence | null => {
  const first = example.value;
  if (!first) return null;
  if (siteCount.value === 1) return { text: t('address.consequences.sites.one'), codes: {} };
  return {
    text: t('address.consequences.sites.many', { count: siteCount.value }),
    codes: {
      from: siteUrl(props.contentBase, props.group, first.slug),
      to: siteUrl(props.contentBase, typed.value, first.slug),
    },
  };
});

// -- Editing -----------------------------------------------------------------

function start(): void {
  editing.value = true;
}

watch(editing, async (shown) => {
  if (!shown) {
    sheet.value?.hide?.();
    return;
  }
  await nextTick();
  sheet.value?.show?.();
});

/** What waits for the sheet to be gone, as `afterClose` does for the dialog. */
let afterSheetClose: (() => void) | null = null;

/**
 * The close button and the sheet itself both end up here, the button first and
 * the sheet once it is gone. Closing is cancelling: what was typed goes.
 */
function sheetClosed(): void {
  editing.value = false;
  typed.value = '';
  verdict.value = null;
  judged.value = false;
  leftVerdict.value = null;
  // Once judged, the list never goes back to showing its hints by itself, and
  // it stays in the sheet for the next time.
  /* v8 ignore start -- gone only when the sheet unmounts with the role taken away, which jsdom cannot close */
  if (list.value) list.value.judging = false;
  /* v8 ignore stop */
  const next = afterSheetClose;
  afterSheetClose = null;
  // Behind the sheet the page is inert, so this lands only once the sheet is gone.
  (next ?? (() => startButton.value?.focus()))();
}

function inputValue(event: Event): string {
  return (
    (event as CustomEvent<{ value?: string }>).detail?.value ?? (event.target as HTMLInputElement).value
  );
}

function edit(event: Event): void {
  typed.value = slugifyTyped(inputValue(event));
  verdict.value = null;
  leftVerdict.value = null;
  notice.value = null;
}

/**
 * Judged when it is left, not while it is typed in. An empty field is only
 * judged on submit: leaving it says nothing about the address yet.
 */
function left(): void {
  if (typed.value === '') return;
  judged.value = true;
  if (!typedIsAddress.value) leftVerdict.value = t('address.field.rule');
  else if (typed.value === current.value) leftVerdict.value = t('address.field.differs');
}

/** Emptied first: a live region only speaks when its words change. */
async function say(sentence: Sentence): Promise<void> {
  notice.value = null;
  await nextTick();
  notice.value = sentence;
}

// -- Asking, and changing ----------------------------------------------------

/** The site in the group that gave this address up and is redirected to from it. */
interface FormerOwner {
  title: string;
  date: string;
}

/** Set while the dialog asks to take the address from another site. */
const release = ref<FormerOwner | null>(null);

function formerOwner(slug: string): FormerOwner | null {
  if (props.kind !== 'site') return null;
  for (const sibling of props.siblings ?? []) {
    const previous = sibling.previousSlugs.find((p) => p.slug === slug);
    if (previous) return { title: sibling.title, date: lastRedirectDay(previous.redirectsUntil) };
  }
  return null;
}

function ask(slug: string, owner: FormerOwner | null = null): void {
  today.value = new Date();
  notice.value = null;
  verdict.value = null;
  pending.value = slug;
  release.value = owner;
  open.value = true;
}

/**
 * The browser has judged the field by now; this is only for a slug that is
 * not an address, or is the one it already is.
 */
function submit(): void {
  judged.value = true;
  if (!typedIsNew.value) return;
  const owner = formerOwner(typed.value);
  if (owner && !props.canRelease) {
    void showVerdict(t('address.release.reserved', { ...owner }));
    return;
  }
  ask(typed.value, owner);
}

const dialogTitle = computed(() =>
  t(`address.confirm.title.${props.kind}`, {
    from: short(current.value),
    to: short(pending.value),
  }),
);

const dialogText = computed(() => {
  const params = { count: siteCount.value, date: untilDate.value };
  if (props.kind === 'group' && siteCount.value > 1) return t('address.confirm.text.group.many', params);
  if (props.kind === 'group' && siteCount.value === 1) return t('address.confirm.text.group.one', params);
  const text = t('address.confirm.text', params);
  return release.value ? `${t('address.release.text', { ...release.value })} ${text}` : text;
});

/**
 * What waits for the dialog to be gone. The page behind a modal is out of
 * reach until then, so a status line or a verdict that appears in the meantime
 * is not heard, and the dialog gives the focus back only when it closes.
 */
let afterClose: (() => void) | null = null;

function closed(): void {
  open.value = false;
  const next = afterClose;
  afterClose = null;
  next?.();
}

function refusalText(code: string | undefined): string | null {
  switch (code) {
    case 'SLUG_INVALID':
      return t('address.error.invalid');
    case 'SLUG_EXISTS':
      return t(`address.error.exists.${props.kind}`);
    case 'TOO_MANY_PREVIOUS_SLUGS':
      return t(`address.error.tooMany.${props.kind}`);
    default:
      return null;
  }
}

async function showVerdict(text: string): Promise<void> {
  verdict.value = text;
  // After the render, so the field is already described by its verdict when
  // the focus lands on it.
  await nextTick();
  field.value?.focus();
}

function reportFailure(failure: unknown): void {
  notices.value?.notify(
    'critical',
    t('address.error.failed'),
    failure instanceof ApiError
      ? (failure.problem.detail ?? failure.problem.title)
      : t('address.error.failed.detail'),
  );
}

function failed(failure: unknown): void {
  const text = failure instanceof ApiError ? refusalText(failure.problem.code) : null;
  open.value = false;
  if (text === null) afterClose = () => reportFailure(failure);
  else if (editing.value) afterClose = () => void showVerdict(text);
  // Changed back from the page: there is no field to say it at.
  else afterClose = () => notices.value?.notify('critical', t('address.error.failed'), text);
}

function succeeded(updated: Group | Site, from: string): void {
  const retired = updated.previousSlugs.find((p) => p.slug === from);
  const message: Sentence = {
    text: t('address.changed', {
      date: retired ? lastRedirectDay(retired.redirectsUntil) : untilDate.value,
    }),
    codes: { from: short(from), to: short(updated.slug), command: 'plak publish' },
  };
  open.value = false;
  const done = (): void => {
    void say(message);
    // The button that opened the dialog may be gone: an old address that is current now.
    startButton.value?.focus();
  };
  // The page is heard only once both overlays are gone: the dialog first, then the sheet.
  afterClose = editing.value
    ? () => {
        afterSheetClose = done;
        editing.value = false;
      }
    : done;
  emit('renamed', updated);
}

async function change(): Promise<void> {
  const from = current.value;
  busy.value = true;
  let updated: Group | Site;
  try {
    updated =
      props.kind === 'group'
        ? await plak.setGroupSlug(props.group, pending.value)
        : await plak.setSiteSlug(props.group, from, pending.value, release.value !== null);
  } catch (failure) {
    failed(failure);
    return;
  } finally {
    busy.value = false;
  }
  succeeded(updated, from);
}
</script>

<template>
  <section :aria-labelledby="`heading-${testPrefix}`">
    <Notices ref="notices" />

    <nldd-container layout="stack" gap="8">
      <nldd-title :size="4">
        <h2 :id="`heading-${testPrefix}`">{{ t(`address.${kind}.heading`) }}</h2>
      </nldd-title>

      <nldd-container layout="stack" gap="16">
        <nldd-rich-text :data-testid="`${testPrefix}-current`">
          <p><CodeSentence v-bind="currentSentence" /></p>
        </nldd-rich-text>

        <nldd-container v-if="canChange" layout="stack" gap="8">
          <nldd-button-group orientation="horizontal">
            <nldd-button
              ref="startButton"
              variant="secondary"
              type="button"
              :text="t('address.change')"
              :data-testid="`${testPrefix}-start`"
              @click="start"
            ></nldd-button>
          </nldd-button-group>

          <nldd-rich-text role="status" :data-testid="`${testPrefix}-notice`">
            <p v-if="notice"><CodeSentence v-bind="notice" /></p>
          </nldd-rich-text>
        </nldd-container>

        <nldd-container v-if="previousSlugs.length > 0" layout="stack" gap="8">
          <nldd-title :size="5">
            <h3 :id="`heading-${testPrefix}-previous`">{{ t('address.previous.heading') }}</h3>
          </nldd-title>
          <nldd-list
            type="form"
            variant="box-base"
            :accessible-label="t('address.previous.heading')"
            :data-testid="`${testPrefix}-previous`"
          >
            <nldd-list-item v-for="previous in previousSlugs" :key="previous.slug" size="md">
              <!-- The text wraps where it has to, so a long address stays in the row
                   on a 320 px screen at 200 percent text (WCAG 1.4.10). -->
              <nldd-text-cell
                :supporting-text="t('address.previous.until', { date: lastRedirectDay(previous.redirectsUntil) })"
              >
                <code translate="no">{{ short(previous.slug) }}</code>
              </nldd-text-cell>
              <nldd-cell v-if="canChange" width="fit-content">
                <nldd-button
                  variant="secondary"
                  size="sm"
                  type="button"
                  :text="t('address.restore')"
                  :accessible-label="t('address.restore.label', { address: shown(previous.slug) })"
                  :data-testid="`${testPrefix}-restore-${previous.slug}`"
                  @click="ask(previous.slug)"
                ></nldd-button>
              </nldd-cell>
            </nldd-list-item>
          </nldd-list>
        </nldd-container>

        <template v-if="canChange">
          <!-- Overlays belong on the document root (NLDD), see PublishSheet. -->
          <Teleport to="body">
            <nldd-sheet
              ref="sheet"
              placement="right"
              :accessible-label="t(`address.sheet.title.${kind}`)"
              :data-testid="`${testPrefix}-sheet`"
              @close="sheetClosed"
            >
              <!-- nldd-page brings the scroller the sheet expects; see PublishSheet. -->
              <nldd-page>
                <nldd-container padding="24" gap="16">
                  <nldd-title :size="4">
                    <h2>{{ t(`address.sheet.title.${kind}`) }}</h2>
                    <!-- Close belongs at the top right, not up against the primary
                         button (NLDD guideline: give the way out physical distance). -->
                    <nldd-button
                      slot="end"
                      variant="neutral-transparent"
                      size="sm"
                      :text="t('address.sheet.close')"
                      type="button"
                      :data-testid="`${testPrefix}-close`"
                      @click="sheetClosed"
                    ></nldd-button>
                  </nldd-title>

                  <nldd-banner
                    v-if="isPublic"
                    variant="warning"
                    :text="t(`address.public.${kind}`)"
                    :supporting-text="t(`address.public.${kind}.detail`)"
                    :data-testid="`${testPrefix}-public`"
                  ></nldd-banner>

                  <nldd-form :data-testid="`${testPrefix}-form`" @submit.prevent="submit">
                    <nldd-form-field :label="t('address.field.label')">
                      <!-- The sheet moves the focus to whatever has autofocus when it opens. -->
                      <nldd-text-field
                        ref="field"
                        :name="testPrefix"
                        required
                        autofocus
                        autocomplete="off"
                        no-spellcheck
                        :pattern="SLUG_PATTERN"
                        :value="typed"
                        :invalid="verdict !== null || ruleFails || undefined"
                        :unmet="verdict !== null ? `${testPrefix}-server` : undefined"
                        :data-testid="testPrefix"
                        @input="edit"
                        @change="left"
                      ></nldd-text-field>
                      <!-- The list reads the value off the field on every input
                           event, which is the value before it was made into an
                           address; `value` hands it the one the page keeps. -->
                      <nldd-validation-list ref="list" :value="typed">
                        <nldd-validation-item :id="`${testPrefix}-required`" required>
                          {{ t('address.field.required') }}
                        </nldd-validation-item>
                        <nldd-validation-item :id="`${testPrefix}-rule`" :match="SLUG_MATCH">
                          {{ t('address.field.rule') }}
                        </nldd-validation-item>
                        <!-- Looks ahead, so any value but this one has it. -->
                        <nldd-validation-item :id="`${testPrefix}-differs`" :match="`^(?!${current}$)`">
                          {{ t('address.field.differs') }}
                        </nldd-validation-item>
                        <nldd-validation-item :id="`${testPrefix}-server`">
                          {{ verdict }}
                        </nldd-validation-item>
                      </nldd-validation-list>
                      <nldd-form-field-help-text v-if="typed !== ''">
                        <CodeSentence :text="t('address.field.preview')" :codes="{ address: preview }" />
                      </nldd-form-field-help-text>
                    </nldd-form-field>

                    <!-- No live region: it changes with every key, and is read on the way to the button. -->
                    <nldd-rich-text v-if="typedIsNew" :data-testid="`${testPrefix}-consequences`">
                      <p>{{ t('address.consequences.self') }}</p>
                      <ul>
                        <li><CodeSentence v-bind="workflow" /></li>
                        <li>
                          <CodeSentence
                            :text="t('address.consequences.content', { date: untilDate })"
                            :codes="{ canonical: 'canonical', ogUrl: 'og:url' }"
                          />
                        </li>
                        <li>
                          <CodeSentence
                            :text="t('address.consequences.secretLink', { date: untilDate })"
                            :codes="{ key: '?key=' }"
                          />
                        </li>
                      </ul>
                      <p>{{ t('address.consequences.changes') }}</p>
                      <ul>
                        <li v-if="sitesSentence"><CodeSentence v-bind="sitesSentence" /></li>
                        <li>{{ t('address.consequences.redirect', { date: untilDate }) }}</li>
                        <li>{{ t(`address.consequences.expiry.${kind}`) }}</li>
                        <li>{{ t('address.consequences.login') }}</li>
                        <li>{{ t('address.consequences.previews', { date: untilDate }) }}</li>
                        <li>{{ t('address.consequences.undo', { date: untilDate }) }}</li>
                      </ul>
                    </nldd-rich-text>

                    <nldd-form-actions>
                      <nldd-button
                        variant="primary"
                        type="submit"
                        :text="t('address.change')"
                        :data-testid="`${testPrefix}-change`"
                      ></nldd-button>
                    </nldd-form-actions>
                  </nldd-form>

                  <!-- After Enter the field has the focus already, so focusing it says
                       nothing. A field that was left has lost it. This is what a
                       screen reader hears of the verdict. -->
                  <div
                    v-if="verdict ?? leftVerdict"
                    role="alert"
                    class="visually-hidden"
                    :data-testid="`${testPrefix}-alert`"
                  >
                    {{ verdict ?? leftVerdict }}
                  </div>
                </nldd-container>
              </nldd-page>
            </nldd-sheet>
          </Teleport>

          <ConfirmModal
            :open="open"
            :title="dialogTitle"
            :text="dialogText"
            :keep-label="t('address.confirm.keep')"
            :confirm-label="release ? t('address.release.confirm') : t('address.confirm.confirm')"
            confirm-variant="secondary"
            :busy="busy"
            @confirm="change"
            @close="closed"
          />
        </template>

        <nldd-rich-text v-else :data-testid="`${testPrefix}-readonly`">
          <p>{{ readOnlyText }}</p>
        </nldd-rich-text>
      </nldd-container>
    </nldd-container>
  </section>
</template>
