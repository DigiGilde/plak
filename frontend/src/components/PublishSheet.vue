<script setup lang="ts">
/**
 * "Zet een site online": a flow from file to published site. Behind one button
 * it combines three tasks (create a group, create a site, upload a version),
 * because the user sees that as one task.
 *
 * The address field is always there, prefilled live from the title through
 * `slugify` (NLDD: always show what is always editable, rather than hiding it
 * behind a button that has to be discovered first). Editing it by hand stops
 * the title from overwriting it; clearing it resumes following the title, and
 * what is typed is normalised on the spot.
 *
 * Access is the group default with whatever is changed here on top: the base
 * plus the same two extras as the Toegang tab, because the base "only through a link or an invitation"
 * without an extra publishes a site nobody can reach.
 *
 * `groups` is filtered down to `eligibleGroups` first: a group where the
 * user has no group role editor/admin never reaches the picker, since
 * creating a site there 403s (`create_site` in backend/src/plak/api/admin.py
 * demands a group role, with no platform-admin bypass; the `/me` docstring
 * says as much: a platform admin manages people and groups, and needs a
 * visible group role of their own for content). `eligibleGroups` then decides
 * the shape of the middle part: none of them does not strand the flow but
 * asks for a name and creates a fresh group along the way (backend: every
 * active member may do that, regardless of their role elsewhere); one asks
 * nothing; more than one offers the choice.
 */
import { computed, nextTick, ref, watch } from 'vue';

import { ApiError } from '@/api/client';
import type { Access, AccessBase, Group, Me, Site } from '@/api/types';
import { ACCESS_BASE_VALUES } from '@/api/types';
import ErrorBanner from '@/components/ErrorBanner.vue';
import { resolveDroppedFile, useDropState } from '@/composables/fileDrop';
import { mayCreateSiteIn } from '@/composables/roles';
import { SLUG_MATCH, SLUG_PATTERN, SLUG_RE, slugify, slugifyTyped } from '@/composables/slug';
import {
  accessBaseHint,
  accessBaseLabel,
  accessSummary,
  contentUrl,
  formatNumber,
  inviteesHint,
  inviteesLabel,
  keysHint,
  keysLabel,
} from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  open: boolean;
  /** The groups this user is a member of, before filtering to `eligibleGroups`. */
  groups: Group[];
  /**
   * The logged-in member, to filter `groups` down to the ones they may
   * create a site in. Null (an unfetched or failed `/me`) skips filtering,
   * so it never strands the flow; the create-site call still guards itself.
   */
  me?: Me | null;
  /** Content origin from `/me`; carries the displayed address. */
  contentBase: string;
  /**
   * A file already chosen before the sheet opened, e.g. dropped on the
   * overview or group page. Applied once, on the transition to `open`, the
   * same way a file chosen through the field is.
   */
  initialFile?: File | null;
  createGroup: (name: string, slug: string) => Promise<Group>;
  createSite: (groupSlug: string, title: string, slug: string) => Promise<Site>;
  setAccess: (groupSlug: string, siteSlug: string, access: Access) => Promise<Site>;
  publish: (groupSlug: string, siteSlug: string, file: File) => Promise<void>;
}>();

const emit = defineEmits<{
  'update:open': [boolean];
  groupCreated: [Group];
  created: [Site];
  published: [Site];
}>();

/**
 * Access a fresh group starts on in the backend (`create_group`). It lives
 * here because the flow has to show the choice before the button, and at that
 * moment the group does not exist yet.
 */
const ACCESS_NEW_GROUP: Access = { base: 'site_team', keys: false, invitees: false };

const sheetEl = ref<HTMLElement & { show: () => void; hide: () => void }>();
const fileField = ref<HTMLElement>();

const file = ref<File | null>(null);
const title = ref('');
const slug = ref('');
const slugEdited = ref(false);
const groupSlug = ref('');
const groupName = ref('');
const baseEdited = ref(false);
const chosenBase = ref<AccessBase | null>(null);
// Null means: whatever the group default says. Only a switch the user touched
// overrules it, so a group default with an extra on stays on.
const chosenKeys = ref<boolean | null>(null);
const chosenInvitees = ref<boolean | null>(null);
const busy = ref(false);
const error = ref<unknown>(null);

const { isOver: dropTargetActive, onDragEnter, onDragOver, onDragLeave, reset: resetDropState } = useDropState();
const dropError = ref<string | null>(null);

const fileMissing = ref(false);
const titleEmpty = ref(false);
const slugInvalid = ref(false);
const groupNameEmpty = ref(false);
const slugServerError = ref<string | null>(null);
const groupServerError = ref<string | null>(null);

// What a half-succeeded attempt already produced. A second click on "Zet
// online" skips those steps; without this a retry would hit a 409 on the group
// or site it just created itself.
const createdGroup = ref<Group | null>(null);
const createdSite = ref<Site | null>(null);

const eligibleGroups = computed<Group[]>(() =>
  props.me == null ? props.groups : props.groups.filter((group) => mayCreateSiteIn(props.me, group.slug)),
);

/** True only for someone with no group membership at all, not merely no
 * eligible one; that case gets its own explanation in the group field. */
const noGroups = computed(() => props.groups.length === 0);
const noEligibleGroup = computed(() => eligibleGroups.value.length === 0);
const choiceNeeded = computed(() => eligibleGroups.value.length > 1);

/** The group being published into, once it exists. */
const chosenGroup = computed<Group | null>(
  () => createdGroup.value ?? eligibleGroups.value.find((group) => group.slug === groupSlug.value) ?? null,
);

/** The address that will apply, even when the group still has to be created. */
const addressGroupSlug = computed(() => chosenGroup.value?.slug ?? slugify(groupName.value));

const address = computed(() =>
  contentUrl(props.contentBase, `/${addressGroupSlug.value}/${slug.value}/`),
);

/** Only show the address once both halves exist; half a URL is a lie. */
const addressKnown = computed(() => addressGroupSlug.value !== '' && slug.value !== '');

/**
 * The sentence around the address, cut in two at the placeholder. The address
 * itself is bold, and that needs an element of its own; splitting the message
 * instead of gluing two fragments keeps the word order the catalogue's, so a
 * language that puts the address first still reads right.
 */
const addressSentence = computed(() => {
  const [before = '', after = ''] = t('publish.sheet.address.known', {
    address: '\u0000',
  }).split('\u0000');
  return { before, after };
});

/** Access a fresh site would get from its group, shown until chosen otherwise. */
const defaultAccess = computed<Access>(
  () => chosenGroup.value?.defaultAccess ?? ACCESS_NEW_GROUP,
);

/**
 * The group default, with whatever the user changed on top of it. The two
 * extras belong here as well as on the Toegang tab: without them "Niemand
 * standaard" would publish a site nobody can reach. Turning on the secret link
 * makes the first key on the "klaar" screen (pages/Done.vue); the invitee list
 * is filled on the Toegang tab afterwards.
 */
const effectiveAccess = computed<Access>(() => ({
  base: baseEdited.value && chosenBase.value ? chosenBase.value : defaultAccess.value.base,
  keys: chosenKeys.value ?? defaultAccess.value.keys,
  invitees: chosenInvitees.value ?? defaultAccess.value.invitees,
}));

/** Who can really see it, plus what still follows after the publish. */
const accessExplanation = computed(() => {
  const parts = [accessSummary(effectiveAccess.value)];
  if (effectiveAccess.value.keys) {
    parts.push(t('publish.sheet.extras.keysAfter'));
  }
  if (effectiveAccess.value.invitees) {
    parts.push(t('publish.sheet.extras.inviteesAfter'));
  }
  return parts.join(' ');
});

function sameAccess(one: Access, other: Access): boolean {
  return one.base === other.base && one.keys === other.keys && one.invitees === other.invitees;
}

/**
 * The title follows the file name, so the ordinary path costs no typing.
 * `index` is the exception: that is the name of the mechanism, not of the site,
 * so there the field stays empty and the flow does ask the question.
 */
function titleFromFileName(name: string): string {
  const bare = name.replace(/\.(tar\.gz|tgz|zip|html?)$/i, '');
  const words = bare.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (words === '' || words.toLowerCase() === 'index') return '';
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Whether the field's own input holds the file. A drop hands it over through a
 * DataTransfer, and then the field is the one that shows the name and answers
 * for its own required rule. False means the handover did not take and our
 * state is all there is.
 */
const fieldHoldsFile = ref(false);

/** The field's native input, which lives in its shadow root. Reached
 * defensively: it is a private detail of the component, so everything that
 * depends on it falls back silently. */
function fileInput(): HTMLInputElement | null {
  const field = fileField.value;
  return (
    (field?.shadowRoot?.querySelector('input[type=file]') as HTMLInputElement | null) ??
    (field?.querySelector('input[type=file]') as HTMLInputElement | null)
  );
}

/**
 * Hands a dropped file to the field's own input, so the field shows the name
 * and its required rule is satisfied (without this the form refuses to submit
 * after a drop). A file input rejects a plain assignment but accepts a FileList
 * built with DataTransfer.
 *
 * The change event is composed on purpose: an uncomposed one would stay inside
 * the shadow root, where nldd-file-field listens for it.
 */
/* v8 ignore start -- jsdom (`environment: 'jsdom'` in vitest.config) has no
 * native DataTransfer constructor, so `new DataTransfer()` below always
 * throws under test; every drag-and-drop test therefore builds its own
 * `fakeDataTransfer` stand-in (see PublishSheet.test.ts) instead of a real
 * one, and this success path of the hand-over can never run in a unit test. */
function handOverToField(chosen: File): boolean {
  const input = fileInput();
  if (!input || typeof DataTransfer === 'undefined') return false;
  try {
    const data = new DataTransfer();
    data.items.add(chosen);
    input.files = data.files;
    if (input.files?.length !== 1) return false;
    input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  } catch {
    return false;
  }
  return true;
}
/* v8 ignore stop */

/** Shared by the file field and by a drop, so both fill the same state the
 * same way. `fromField` marks the call that the field itself caused, which is
 * the one that must not hand the file back to it. */
function setFile(chosen: File | null, fromField = false): void {
  file.value = chosen;
  if (!file.value) {
    fieldHoldsFile.value = false;
    return;
  }
  fileMissing.value = false;
  dropError.value = null;
  if (fromField) fieldHoldsFile.value = true;
  void adoptIntoField(file.value, fromField);
  if (title.value === '') {
    title.value = titleFromFileName(file.value.name);
    onTitleInput();
  }
}

/**
 * Gets the file into the field and the field out of the red.
 *
 * The retry after a render is for the file that arrives before the sheet has
 * drawn its field (`initialFile`). The wait before the mark comes off is for a
 * different reason: nldd-validation-list hands its verdict to the control, and
 * it only learns the new value on the next render. Until then the control
 * counts as failing its required rule, and nldd-form marks it on the very
 * input event that was meant to clear it.
 */
async function adoptIntoField(chosen: File, fromField: boolean): Promise<void> {
  if (!fromField) {
    /* v8 ignore start -- handOverToField never succeeds under jsdom (see its
     * own comment above), so this consequent can never run under test. */
    if (handOverToField(chosen)) {
      fieldHoldsFile.value = true;
    /* v8 ignore stop */
    } else {
      await nextTick();
      if (file.value !== chosen) return;
      fieldHoldsFile.value = handOverToField(chosen);
    }
  }
  await nextTick();
  /* v8 ignore start -- guards a second drop landing between this tick and the
   * previous one; not deterministically reproducible from outside Vue's own
   * microtask scheduling. */
  if (file.value !== chosen) return;
  /* v8 ignore stop */
  fileField.value?.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  await nextTick();
  /* v8 ignore start -- the false side needs a third drop landing in this last
   * tick, same reproducibility problem as the guard above. */
  if (file.value === chosen) fileField.value?.removeAttribute('invalid');
  /* v8 ignore stop */
}

function onFileChoice(event: Event): void {
  const detail = (event as CustomEvent<{ files?: File[] }>).detail;
  const files = detail?.files ?? (event.target as HTMLInputElement | null)?.files;
  setFile(files?.[0] ?? null, true);
}

/**
 * Only for the file the field could not be given: then the chip is the one
 * thing on screen that says a file is chosen. Once the field holds it, the
 * field says so itself and a chip beside it would tell the same story twice.
 */
const chosenFile = computed(() =>
  file.value === null || fieldHoldsFile.value
    ? null
    : `${file.value.name} (${fileSize(file.value.size)})`,
);

/**
 * The field answers for the required rule only while it is the one holding the
 * file. A file it could not be given would otherwise leave it marking itself
 * invalid, and blocking the submit, over a file that is plainly there; `check`
 * covers the genuinely empty case either way.
 */
const fieldRequired = computed(() => file.value === null || fieldHoldsFile.value);

function fileSize(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  const units = ['kB', 'MB', 'GB'];
  let value = bytes / 1000;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${formatNumber(value, { maximumFractionDigits: 1 })} ${units[unit]}`;
}

/** Clears what the field and a drop both write, so one control undoes either.
 * nldd-file-field owns a FileList of its own and offers no public clear, so
 * the input it wraps is emptied instead and told about it; the component
 * follows that the way it follows a real choice. */
function clearFile(): void {
  const input = fileInput();
  /* v8 ignore start -- same jsdom limitation as handOverToField: the native
   * input this reaches for only ever exists after a real hand-over, which
   * never happens under test (see handOverToField's comment). */
  if (input) {
    input.value = '';
    input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
  }
  /* v8 ignore stop */
  file.value = null;
  fieldHoldsFile.value = false;
  dropError.value = null;
  fileField.value?.removeAttribute('invalid');
}

/** The whole sheet is a drop target (product requirement: a small dedicated
 * zone is too easy to miss), so this sits on the sheet root rather than on
 * the file field alone. */
function onSheetDrop(event: DragEvent): void {
  event.preventDefault();
  resetDropState();
  const { file: dropped, error: rejection } = resolveDroppedFile(event.dataTransfer);
  if (rejection) {
    dropError.value = rejection;
    return;
  }
  if (dropped) setFile(dropped);
}

function onTitleInput(): void {
  if (!slugEdited.value) {
    slug.value = slugify(title.value);
  }
}

/**
 * A hand-typed slug sticks; clearing it by hand resumes following the title,
 * since an empty address is never what anyone wants left on screen. What is
 * typed is normalised on the spot, so a space or a capital cannot survive
 * until the submit that then refuses it.
 */
function onSlugInput(value: string): void {
  const typed = slugifyTyped(value);
  slug.value = typed;
  slugEdited.value = typed !== '';
  if (!slugEdited.value) {
    slug.value = slugify(title.value);
  }
}

function chooseBase(value: AccessBase): void {
  baseEdited.value = true;
  chosenBase.value = value;
}

function switched(event: Event): boolean {
  return Boolean((event as CustomEvent<{ checked?: boolean }>).detail?.checked);
}

function toggleKeys(event: Event): void {
  chosenKeys.value = switched(event);
}

function toggleInvitees(event: Event): void {
  chosenInvitees.value = switched(event);
}

function resetForm(): void {
  clearFile();
  title.value = '';
  slug.value = '';
  slugEdited.value = false;
  groupName.value = '';
  baseEdited.value = false;
  chosenBase.value = null;
  chosenKeys.value = null;
  chosenInvitees.value = null;
  fileMissing.value = false;
  titleEmpty.value = false;
  slugInvalid.value = false;
  groupNameEmpty.value = false;
  slugServerError.value = null;
  groupServerError.value = null;
  error.value = null;
  dropError.value = null;
  resetDropState();
  busy.value = false;
  createdGroup.value = null;
  createdSite.value = null;
}

/** No disabled button: the click is always allowed, the reason shows after it. */
function check(): boolean {
  fileMissing.value = file.value === null;
  titleEmpty.value = title.value.trim() === '';
  slugInvalid.value = !SLUG_RE.test(slug.value);
  groupNameEmpty.value =
    noEligibleGroup.value && createdGroup.value === null && !SLUG_RE.test(slugify(groupName.value));
  return !fileMissing.value && !titleEmpty.value && !slugInvalid.value && !groupNameEmpty.value;
}

function rememberServerError(e: unknown, field: 'site' | 'group'): boolean {
  if (!(e instanceof ApiError) || (e.problem.status !== 422 && e.problem.status !== 409)) return false;
  const text = e.problem.detail ?? e.problem.title;
  if (field === 'group') {
    groupServerError.value = text;
  } else {
    slugServerError.value = text;
  }
  return true;
}

async function putOnline(): Promise<void> {
  if (busy.value) return;
  error.value = null;
  slugServerError.value = null;
  groupServerError.value = null;
  if (!check()) return;

  busy.value = true;
  try {
    let group = chosenGroup.value;
    if (!group) {
      try {
        group = await props.createGroup(groupName.value.trim(), slugify(groupName.value));
      } catch (e) {
        if (!rememberServerError(e, 'group')) error.value = e;
        return;
      }
      createdGroup.value = group;
      emit('groupCreated', group);
    }

    let site = createdSite.value;
    if (!site) {
      try {
        site = await props.createSite(group.slug, title.value.trim(), slug.value);
      } catch (e) {
        if (!rememberServerError(e, 'site')) error.value = e;
        return;
      }
      createdSite.value = site;
      emit('created', site);
    }

    // Applied before the upload, so the first live version is never visible
    // more widely than what was chosen here.
    if (!sameAccess(effectiveAccess.value, site.access)) {
      try {
        site = await props.setAccess(group.slug, site.slug, effectiveAccess.value);
      } catch (e) {
        error.value = e;
        return;
      }
      createdSite.value = site;
    }

    await props.publish(group.slug, site.slug, file.value!);
    emit('published', site);
    resetForm();
    emit('update:open', false);
  } catch (e) {
    error.value = e;
  } finally {
    busy.value = false;
  }
}

function onClose(): void {
  resetForm();
  emit('update:open', false);
}

// The groups come from a request still in flight. Track them immediately, not
// only on open: nldd-dropdown reads the selected option when the <select> is
// slotted and after that only refreshes its label on a change. A choice made
// later therefore never reaches it.
watch(
  eligibleGroups,
  (groups) => {
    if (!groups.some((group) => group.slug === groupSlug.value)) {
      groupSlug.value = groups[0]?.slug ?? '';
    }
  },
  { immediate: true },
);

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      sheetEl.value?.hide?.();
      return;
    }
    if (props.initialFile) setFile(props.initialFile);
    await nextTick();
    sheetEl.value?.show?.();
  },
  { immediate: true },
);
</script>

<template>
  <!-- Overlays belong on the document root (NLDD): as a light-DOM child of a
       layout component a sheet can end up inert or trapped inside a stacking
       context. -->
  <Teleport to="body">
    <!-- The whole sheet is the drop target, not a small zone within it: a
         drag anywhere inside counts, so the handlers sit on the sheet root
         rather than on the file field. useDropState's counter needs enter/
         leave bound to this one element (see its doc comment). -->
    <nldd-sheet
      ref="sheetEl"
      placement="right"
      :accessible-label="t('publish.sheet.heading')"
      class="publish-sheet"
      :class="{ 'publish-sheet--dragover': dropTargetActive }"
      @close="onClose"
      @dragenter="onDragEnter"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onSheetDrop"
    >
      <!-- nldd-page, not the container alone: the sheet is `overflow: hidden`
           with `--context-scroll-mode: nested`, so whatever it slots has to
           bring the scroller. Without the page a short viewport clips the
           form and the submit button cannot be reached. -->
      <nldd-page>
        <nldd-container padding="24" gap="16">
          <nldd-title :size="4">
            <h2>{{ t('publish.sheet.heading') }}</h2>
            <!-- Close belongs at the top right, not up against the primary
                 button (NLDD guideline: give the way out physical distance). -->
            <nldd-button
              slot="end"
              variant="neutral-transparent"
              size="sm"
              :text="t('publish.sheet.close')"
              type="button"
              data-testid="publiceer-sluiten"
              @click="onClose"
            ></nldd-button>
          </nldd-title>

          <nldd-banner
            v-if="dropTargetActive"
            variant="accent"
            :text="t('publish.sheet.drop')"
            data-testid="publiceer-sleep-actief"
          ></nldd-banner>

          <nldd-banner
            v-if="dropError"
            variant="critical"
            dismissible
            :text="dropError"
            data-testid="publiceer-sleep-fout"
            @dismiss="dropError = null"
          ></nldd-banner>

          <ErrorBanner v-if="error" :error="error" />

          <nldd-banner
            v-if="createdSite && error"
            variant="warning"
            size="sm"
            :text="t('publish.sheet.halfway.title')"
            :supporting-text="t('publish.sheet.halfway.detail')"
            data-testid="publiceer-halfweg"
          ></nldd-banner>

          <nldd-form @submit.prevent="putOnline">
            <nldd-form-field
              :label="t('publish.sheet.file.label')"
              :supporting-label="t('publish.sheet.file.hint')"
            >
              <nldd-file-field
                ref="fileField"
                name="bestand"
                accept=".zip,.tar.gz,.tgz,.html"
                :required="fieldRequired || undefined"
                :invalid="fileMissing || undefined"
                data-testid="publiceer-bestand"
                @change="onFileChoice"
              ></nldd-file-field>
              <!-- The fallback for a file the field could not be given: then
                   this is what says a file is chosen, and clears it again. -->
              <nldd-token
                v-if="chosenFile"
                control="dismiss"
                :dismiss-text="t('publish.sheet.file.clear')"
                data-testid="publiceer-bestand-gekozen"
                @dismiss="clearFile"
                >{{ chosenFile }}</nldd-token
              >
              <!-- nldd-validation-list reads `control.value` and nldd-file-field
                   has none: without this value the required rule fails even with
                   a file present. -->
              <nldd-validation-list :value="file?.name ?? ''">
                <nldd-validation-item id="publiceer-bestand-vereist" required>
                  {{ t('publish.sheet.file.required') }}
                </nldd-validation-item>
              </nldd-validation-list>
            </nldd-form-field>

            <nldd-form-field :label="t('publish.sheet.title.label')">
              <nldd-text-field
                name="titel"
                :value="title"
                required
                :invalid="titleEmpty || undefined"
                data-testid="publiceer-titel"
                @input="(e: CustomEvent) => { title = (e.detail?.value ?? (e.target as HTMLInputElement).value); titleEmpty = false; onTitleInput(); }"
              ></nldd-text-field>
              <nldd-validation-list :value="title">
                <nldd-validation-item id="publiceer-titel-vereist" required>
                  {{ t('publish.sheet.title.required') }}
                </nldd-validation-item>
              </nldd-validation-list>
            </nldd-form-field>

            <!-- The group sits by the address because it is the first half of it. -->
            <nldd-form-field v-if="choiceNeeded" :label="t('publish.sheet.group.label')">
              <nldd-dropdown>
                <!-- v-model, not :value: a select picks through its options, and
                     those do not exist yet at the moment a bound value would be
                     set. -->
                <select v-model="groupSlug" name="groep">
                  <option v-for="group in eligibleGroups" :key="group.slug" :value="group.slug">
                    {{ group.name }}
                  </option>
                </select>
              </nldd-dropdown>
            </nldd-form-field>

            <!-- No eligible group also lands here, not on a dead end: creating
                 a group is open to every active member (backend `create_group`),
                 so it is the way out even for someone whose existing groups
                 all refuse them the editor/beheerder role. -->
            <nldd-form-field
              v-else-if="noEligibleGroup && !createdGroup"
              :label="t('publish.sheet.groupName.label')"
              :supporting-label="
                noGroups
                  ? t('publish.sheet.groupName.hintNone')
                  : t('publish.sheet.groupName.hintNoRole')
              "
            >
              <nldd-text-field
                name="groepnaam"
                :value="groupName"
                required
                :invalid="groupNameEmpty || groupServerError !== null || undefined"
                :unmet="groupServerError !== null ? 'publiceer-groep-server' : undefined"
                data-testid="publiceer-groepnaam"
                @input="(e: CustomEvent) => { groupName = (e.detail?.value ?? (e.target as HTMLInputElement).value); groupNameEmpty = false; }"
              ></nldd-text-field>
              <nldd-validation-list :value="groupName">
                <nldd-validation-item id="publiceer-groepnaam-vereist" required>
                  {{ t('publish.sheet.groupName.required') }}
                </nldd-validation-item>
                <nldd-validation-item id="publiceer-groep-server">
                  {{ groupServerError }}
                </nldd-validation-item>
              </nldd-validation-list>
            </nldd-form-field>

            <!-- The address is always editable, not behind a button that first
                 has to be discovered (NLDD: an always-available control shows
                 itself). It fills in live from the title until hand-edited. -->
            <nldd-form-field
              :label="t('publish.sheet.address.label')"
              :supporting-label="t('publish.sheet.address.hint')"
            >
              <nldd-text-field
                name="slug"
                :value="slug"
                required
                :pattern="SLUG_PATTERN"
                :invalid="slugInvalid || slugServerError !== null || undefined"
                :unmet="slugServerError !== null ? 'publiceer-slug-server' : undefined"
                data-testid="publiceer-slug"
                @input="(e: CustomEvent) => onSlugInput(e.detail?.value ?? (e.target as HTMLInputElement).value)"
              ></nldd-text-field>
              <!-- The list normally reads the value off the field on every input
                   event; here the address is also derived from the title, and it
                   does not see that change. -->
              <nldd-validation-list :value="slug">
                <nldd-validation-item id="publiceer-slug-vereist" required>
                  {{ t('publish.sheet.address.required') }}
                </nldd-validation-item>
                <nldd-validation-item id="publiceer-slug-vorm" hint :match="SLUG_MATCH">
                  {{ t('publish.sheet.address.form') }}
                </nldd-validation-item>
                <nldd-validation-item id="publiceer-slug-server">
                  {{ slugServerError }}
                </nldd-validation-item>
              </nldd-validation-list>
              <nldd-form-field-help-text>
                <span v-if="addressKnown" data-testid="publiceer-adres"
                  >{{ addressSentence.before }}<strong>{{ address }}</strong
                  >{{ addressSentence.after }}</span
                >
                <span v-else data-testid="publiceer-adres-leeg">
                  {{ t('publish.sheet.address.empty') }}
                </span>
              </nldd-form-field-help-text>
            </nldd-form-field>

            <!-- Publishing fixes who may look; choosing that belongs on screen
                 before the button, not only on the site page afterwards. -->
            <nldd-form-field :label="t('publish.sheet.visibility.label')">
              <nldd-list
                type="radiogroup"
                variant="box-base"
                :accessible-label="t('publish.sheet.visibility.label')"
                data-testid="publiceer-zichtbaarheid"
              >
                <nldd-list-item
                  v-for="w in ACCESS_BASE_VALUES"
                  :key="w"
                  radio
                  size="md"
                  :checked="w === effectiveAccess.base || undefined"
                  :data-testid="`publiceer-zichtbaarheid-${w}`"
                  @change="chooseBase(w)"
                >
                  <!-- The row is the radio itself; the button only draws the
                       shape (decorative), otherwise there is a control inside a
                       control. -->
                  <nldd-cell width="fit-content">
                    <nldd-radio-button
                      decorative
                      :checked="w === effectiveAccess.base || undefined"
                    ></nldd-radio-button>
                  </nldd-cell>
                  <nldd-spacer-cell size="12"></nldd-spacer-cell>
                  <nldd-title-cell
                    :size="6"
                    :text="accessBaseLabel(w)"
                    :supporting-text="accessBaseHint(w)"
                  ></nldd-title-cell>
                </nldd-list-item>
              </nldd-list>
            </nldd-form-field>

            <!-- The same two extras as the Toegang tab (components/site/
                 TabAccess.vue): without them that base publishes a
                 site nobody can reach. Each switch sits in a form-field of its
                 own without a label: nldd-switch-field renders no slot, so a
                 help text written inside it never appears, and the field is
                 what gives that text a place. It does not label the control
                 twice either, because _applyAccessibleLabel only writes to a
                 control with `accessibleLabel` or a native input, and a switch
                 field is neither. -->
            <nldd-form-section
              :text="t('publish.sheet.extras.heading')"
              :supporting-text="t('publish.sheet.extras.hint')"
            >
              <nldd-form-field>
                <nldd-switch-field
                  :label="keysLabel()"
                  :checked="effectiveAccess.keys || undefined"
                  data-testid="publiceer-uitzondering-sleutels"
                  @change="toggleKeys"
                ></nldd-switch-field>
                <nldd-form-field-help-text>{{ keysHint() }}</nldd-form-field-help-text>
              </nldd-form-field>

              <nldd-form-field>
                <nldd-switch-field
                  :label="inviteesLabel()"
                  :checked="effectiveAccess.invitees || undefined"
                  data-testid="publiceer-uitzondering-genodigden"
                  @change="toggleInvitees"
                ></nldd-switch-field>
                <nldd-form-field-help-text>{{ inviteesHint() }}</nldd-form-field-help-text>
              </nldd-form-field>

              <!-- The sum of base and extras, which is what someone came for and
                   the one thing three separate controls cannot say. -->
              <nldd-inline-dialog
                icon="eye"
                :text="accessExplanation"
                data-testid="publiceer-toegang-uitleg"
              ></nldd-inline-dialog>
            </nldd-form-section>

            <nldd-form-actions>
              <nldd-button
                variant="primary"
                :text="t('publish.sheet.submit')"
                type="submit"
                :loading="busy || undefined"
                data-testid="publiceer-indienen"
              ></nldd-button>
            </nldd-form-actions>
          </nldd-form>
        </nldd-container>
      </nldd-page>
    </nldd-sheet>
  </Teleport>
</template>

<style scoped>
/* NLDD's own focus-ring tokens, not a hand-rolled colour, for the "a file is
   over the sheet" outline. */
.publish-sheet--dragover {
  outline: var(--semantics-focus-ring-outline);
  outline-offset: var(--semantics-focus-ring-outline-offset);
}
</style>
