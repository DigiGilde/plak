<script setup lang="ts">
/**
 * Toegang tab of the site page: who can view this site.
 *
 * One base ("wie kan deze site bekijken?") plus two extras that widen it.
 * The extras are not narrower levels of the same choice, so they are not radio
 * options: they sit beside the base as switches, and each one brings its own
 * list onto the page when it is on.
 *
 * Those lists are tables with a row menu, the same shape as the Leden tab, not
 * a sidebar: a sheet is for an editing task beside a list; these lists ARE the
 * subject of this tab, so putting them behind a "Beheren" button would hide
 * the one thing the page is about.
 */
import { computed, onMounted, ref, watch } from 'vue';

import * as plak from '@/api/plak';
import type { Access, AccessBase, Invitee, Key } from '@/api/types';
import { ACCESS_BASE_VALUES } from '@/api/types';
import {
  accessBaseHint,
  accessBaseLabel,
  accessSummary,
  formatDate,
  inviteesHint,
  inviteesLabel,
  keysHint,
  keysLabel,
  siteUrl,
} from '@/format';
import { t } from '@/i18n';
import { ApiError } from '@/api/client';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import RowActions, { type RowAction } from '@/components/RowActions.vue';
import SecretLink from '@/components/site/SecretLink.vue';

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const emit = defineEmits<{
  changed: [];
}>();

/** Label, made on, expires, status, menu. */
const KEY_COLUMNS = 'minmax(10rem, 1fr) 8rem 8rem 7rem 3rem';
/** On a phone only the label and the menu survive; the rest moves under it. */
const KEY_COLUMNS_SM = 'minmax(0, 1fr) 3rem';
/** Address, added on, menu. */
const INVITEE_COLUMNS = 'minmax(10rem, 1fr) 9rem 3rem';
const INVITEE_COLUMNS_SM = 'minmax(0, 1fr) 3rem';

const loading = ref(true);
const error = ref<unknown>(null);
const notices = ref<InstanceType<typeof Notices> | null>(null);

const access = ref<Access | null>(null);
const externalSources = ref(true);
const sandbox = ref(true);

const invitees = ref<Invitee[]>([]);
const inviteeEmail = ref('');
const inviteeEmpty = ref(false);
const inviteeError = ref<string | null>(null);

const keys = ref<Key[]>([]);
const keyLabel = ref('');
const keyDays = ref('90');
const keyError = ref<string | null>(null);
/** The plaintext of a key just made; it exists only on this screen. */
const newKeyValue = ref<string | null>(null);
const siteAddress = computed(() => siteUrl(props.contentBase, props.group, props.site));
// The template only ever reads this behind `newKeyValue && newKeyLink`, so the
// falsy branch never runs: the `&&` short-circuits before this computed's
// getter is invoked.
/* v8 ignore start -- unreachable: see comment above */
const newKeyLink = computed(() =>
  newKeyValue.value ? `${siteAddress.value}?key=${newKeyValue.value}` : null,
);
/* v8 ignore stop */

const summary = computed(() => (access.value ? accessSummary(access.value) : ''));
/**
 * A public site is visible to everyone, so an extra can add nothing. The
 * switches stay operable (turning the base back down has to keep the lists
 * that were set up), which is precisely why the interface has to say it.
 */
const extrasAreMoot = computed(() => access.value?.base === 'public');

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [detail, inviteeList, keyList] = await Promise.all([
      plak.group(props.group),
      plak.invitees(props.group, props.site),
      plak.keys(props.group, props.site),
    ]);
    const siteRow = detail.sites.find((p) => p.slug === props.site);
    if (!siteRow) {
      error.value = new ApiError({
        type: 'about:blank',
        title: t('publish.access.unknownSite.title'),
        status: 404,
        detail: t('publish.access.unknownSite.detail', { site: props.site, group: props.group }),
      });
      return;
    }
    access.value = siteRow.access;
    externalSources.value = siteRow.externalSources;
    sandbox.value = siteRow.sandbox;
    invitees.value = inviteeList;
    keys.value = keyList;
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => [props.group, props.site], load);

function inputValue(event: Event): string {
  const detail = (event as CustomEvent<{ value?: string }>).detail;
  return detail?.value ?? (event.target as HTMLInputElement | null)?.value ?? '';
}

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

/**
 * Base and extras travel together in one PUT, because a visitor gets in as
 * soon as one of the three lets them: saving them apart would leave the site
 * open on the old value for as long as the second call takes.
 */
async function save(next: Access, confirmation: string): Promise<void> {
  const previous = access.value;
  access.value = next;
  try {
    const updated = await plak.setAccess(props.group, props.site, next);
    access.value = updated.access;
    notices.value?.notify('success', confirmation, accessSummary(updated.access));
    emit('changed');
  } catch (f) {
    access.value = previous;
    notices.value?.notify(
      'critical',
      t('publish.access.saveFailed'),
      errorText(f, t('publish.access.saveFailedDetail')),
    );
  }
}

function chooseBase(base: AccessBase): void {
  if (!access.value || base === access.value.base) return;
  void save({ ...access.value, base }, t('publish.access.saved'));
}

function switched(event: Event): boolean {
  return Boolean((event as CustomEvent<{ checked?: boolean }>).detail?.checked);
}

function toggleKeys(event: Event): void {
  const next = switched(event);
  if (!access.value || next === access.value.keys) return;
  void save(
    { ...access.value, keys: next },
    next ? t('publish.access.keys.on') : t('publish.access.keys.off'),
  );
}

function toggleInvitees(event: Event): void {
  const next = switched(event);
  if (!access.value || next === access.value.invitees) return;
  void save(
    { ...access.value, invitees: next },
    next ? t('publish.access.invitees.on') : t('publish.access.invitees.off'),
  );
}

async function chooseExternalSources(event: Event): Promise<void> {
  const next = switched(event);
  if (next === externalSources.value) {
    return;
  }
  const previous = externalSources.value;
  externalSources.value = next;
  try {
    const updated = await plak.setExternalSources(props.group, props.site, next);
    externalSources.value = updated.externalSources;
    notices.value?.notify(
      'success',
      t('publish.access.external.saved'),
      updated.externalSources
        ? t('publish.access.external.on')
        : t('publish.access.external.off'),
    );
  } catch (f) {
    externalSources.value = previous;
    notices.value?.notify(
      'critical',
      t('publish.access.external.failed'),
      errorText(f, t('publish.access.saveFailedDetail')),
    );
  }
}

async function chooseSandbox(event: Event): Promise<void> {
  const next = switched(event);
  if (next === sandbox.value) {
    return;
  }
  const previous = sandbox.value;
  sandbox.value = next;
  try {
    const updated = await plak.setSandbox(props.group, props.site, next);
    sandbox.value = updated.sandbox;
    notices.value?.notify(
      'success',
      t('publish.access.sandbox.saved'),
      updated.sandbox ? t('publish.access.sandbox.on') : t('publish.access.sandbox.off'),
    );
  } catch (f) {
    sandbox.value = previous;
    notices.value?.notify(
      'critical',
      t('publish.access.sandbox.failed'),
      errorText(f, t('publish.access.saveFailedDetail')),
    );
  }
}

async function addInvitee(): Promise<void> {
  inviteeError.value = null;
  const identifier = inviteeEmail.value.trim();
  inviteeEmpty.value = identifier === '';
  if (inviteeEmpty.value) return;
  // Provisional row: visible at once, the server confirms in the background.
  const provisional: Invitee = {
    // Empty until the server answers: the id only exists once the add lands.
    id: '',
    groupSlug: props.group,
    siteSlug: props.site,
    identifier,
    addedBy: '',
    addedAt: new Date().toISOString(),
  };
  // If the address is already there, there is nothing to run ahead of; the
  // server reports the duplicate and the row stays where it was.
  const newRow = !invitees.value.some((g) => g.identifier === identifier);
  if (newRow) {
    invitees.value = [...invitees.value, provisional];
  }
  inviteeEmail.value = '';
  try {
    const invitee = await plak.addInvitee(props.group, props.site, identifier);
    // Matched on the address, not on object identity: the ref hands back a
    // reactive proxy of the provisional row, never the object itself.
    invitees.value = invitees.value.map((g) => (g.identifier === identifier ? invitee : g));
  } catch (f) {
    if (newRow) {
      invitees.value = invitees.value.filter((g) => g.identifier !== identifier);
    }
    inviteeEmail.value = identifier;
    inviteeError.value = errorText(f, t('publish.access.invitees.addFailed'));
  }
}

async function removeInvitee(invitee: Invitee): Promise<void> {
  const previous = invitees.value;
  invitees.value = invitees.value.filter((g) => g.identifier !== invitee.identifier);
  try {
    await plak.removeInvitee(props.group, props.site, invitee.id);
  } catch (f) {
    invitees.value = previous;
    notices.value?.notify(
      'critical',
      t('publish.access.invitees.removeFailed', { invitee: invitee.identifier }),
      errorText(f, t('publish.access.invitees.removeFailedDetail')),
    );
  }
}

function inviteeActions(invitee: Invitee): RowAction[] {
  return [
    {
      text: t('publish.access.invitees.remove'),
      icon: 'trash',
      destructive: true,
      details: t('publish.access.invitees.removeHint'),
      testid: `genodigde-verwijderen-${invitee.identifier}`,
      run: () => void removeInvitee(invitee),
    },
  ];
}

// The server makes the value, so there is nothing to run ahead of here:
// create, and leave the link on the page once. The label is optional: an
// empty one gets a date-based default from the server.
async function createKey(): Promise<void> {
  keyError.value = null;
  newKeyValue.value = null;
  const label = keyLabel.value.trim();
  const days = Number(keyDays.value);
  const expiresAt = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
  try {
    const result = await plak.createKey(props.group, props.site, label === '' ? null : label, expiresAt);
    keys.value = [...keys.value, result.key];
    newKeyValue.value = result.value;
    keyLabel.value = '';
    keyDays.value = '90';
  } catch (f) {
    keyError.value = errorText(f, t('publish.access.keys.createFailed'));
  }
}

async function revokeKeyRow(key: Key): Promise<void> {
  const previous = keys.value;
  keys.value = keys.value.map((s) =>
    s.selector === key.selector ? { ...s, status: 'revoked' } : s,
  );
  try {
    await plak.revokeKey(props.group, props.site, key.selector);
  } catch (f) {
    keys.value = previous;
    notices.value?.notify(
      'critical',
      t('publish.access.keys.revokeFailed', { label: key.label }),
      errorText(f, t('publish.access.keys.revokeFailedDetail')),
    );
  }
}

/** A revoked key keeps its row and loses its menu: there is nothing left to do. */
function keyActions(key: Key): RowAction[] {
  if (key.status !== 'active') return [];
  return [
    {
      text: t('publish.access.keys.revoke'),
      icon: 'trash',
      destructive: true,
      details: t('publish.access.keys.revokeHint'),
      testid: `sleutel-intrekken-${key.selector}`,
      run: () => void revokeKeyRow(key),
    },
  ];
}
</script>

<template>
  <Notices ref="notices" />

  <ErrorBanner v-if="error" :error="error" />

  <!-- The tab's structure is there straight away; the indicator only covers it
       once loading takes longer than a second. It stays mounted and switches on
       complete, because every remount restarts that second. -->
  <nldd-activity-indicator
    v-else
    :text="t('publish.access.loading')"
    :complete="!loading || undefined"
  >
    <nldd-container layout="stack" gap="24">
      <section aria-labelledby="kop-basis">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="kop-basis">{{ t('publish.access.base.heading') }}</h2>
          </nldd-title>
          <nldd-list
            type="radiogroup"
            variant="box-base"
            :accessible-label="t('publish.access.base.heading')"
            data-testid="toegang-basis"
          >
            <nldd-list-item
              v-for="w in ACCESS_BASE_VALUES"
              :key="w"
              radio
              size="md"
              :checked="w === access?.base || undefined"
              :data-testid="`basis-${w}`"
              @change="chooseBase(w)"
            >
              <!-- The row is the radio itself; the button only draws the shape
                   (decorative), otherwise there is a control inside a control. -->
              <nldd-cell width="fit-content">
                <nldd-radio-button
                  decorative
                  :checked="w === access?.base || undefined"
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

          <!-- The sum of base and extras, which is the thing someone came here
               to know and the one thing three separate controls cannot say. -->
          <nldd-inline-dialog
            icon="eye"
            :text="summary"
            data-testid="toegang-samenvatting"
          ></nldd-inline-dialog>
        </nldd-container>
      </section>

      <section aria-labelledby="kop-uitzonderingen">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="kop-uitzonderingen">{{ t('publish.access.extras.heading') }}</h2>
            <span slot="subtitle">{{ t('publish.access.extras.intro') }}</span>
          </nldd-title>

          <nldd-banner
            v-if="extrasAreMoot"
            variant="neutral"
            :text="t('publish.access.extras.moot')"
            :supporting-text="t('publish.access.extras.mootDetail')"
            data-testid="uitzonderingen-zinloos"
          ></nldd-banner>

          <nldd-form-field>
            <nldd-switch-field
              :label="keysLabel()"
              :checked="access?.keys || undefined"
              data-testid="uitzondering-sleutels"
              @change="toggleKeys"
            ></nldd-switch-field>
            <nldd-form-field-help-text>{{ keysHint() }}</nldd-form-field-help-text>
          </nldd-form-field>

          <nldd-form-field>
            <nldd-switch-field
              :label="inviteesLabel()"
              :checked="access?.invitees || undefined"
              data-testid="uitzondering-genodigden"
              @change="toggleInvitees"
            ></nldd-switch-field>
            <nldd-form-field-help-text>{{ inviteesHint() }}</nldd-form-field-help-text>
          </nldd-form-field>
        </nldd-container>
      </section>

      <section v-if="access?.keys" aria-labelledby="kop-sleutels">
        <nldd-container layout="stack" gap="16">
          <nldd-title :size="4">
            <h2 id="kop-sleutels">{{ t('access.keys') }}</h2>
            <span slot="subtitle">{{ t('publish.access.keys.intro') }}</span>
          </nldd-title>

          <!-- A successful action should not look like an error: this is the
               link the user came here for. -->
          <nldd-banner
            v-if="newKeyValue && newKeyLink"
            variant="success"
            :text="t('publish.access.keys.created')"
            :supporting-text="t('publish.access.keys.createdDetail')"
            data-testid="nieuwe-sleutel"
          >
            <SecretLink :value="newKeyValue" :site-url="siteAddress" prefix="nieuwe-sleutel" />
            <nldd-button
              slot="actions"
              variant="secondary"
              start-icon="open-new-page"
              :text="t('publish.access.keys.open')"
              :href="newKeyLink"
              target="_blank"
              data-testid="nieuwe-sleutel-openen"
            ></nldd-button>
          </nldd-banner>

          <nldd-table
            class="toegang-tabel"
            :accessible-label="t('access.keys')"
            data-testid="sleutels-lijst"
            :columns="KEY_COLUMNS"
            :sm-columns="KEY_COLUMNS_SM"
          >
            <nldd-table-row slot="header">
              <nldd-text-cell
                :text="`**${t('publish.access.keys.column.label')}**`"
              ></nldd-text-cell>
              <nldd-text-cell
                :text="`**${t('publish.access.keys.column.created')}**`"
                hide-below="md"
              ></nldd-text-cell>
              <nldd-text-cell
                :text="`**${t('publish.access.keys.column.expires')}**`"
                hide-below="md"
              ></nldd-text-cell>
              <nldd-text-cell
                :text="`**${t('publish.access.keys.column.status')}**`"
                hide-below="md"
              ></nldd-text-cell>
              <!-- Named but not shown, as on the Leden tab: a word above one
                   icon button says nothing, and a columnheader without a name
                   is an axe violation. -->
              <nldd-text-cell horizontal-alignment="right">
                <span class="alleen-schermlezer">{{ t('publish.access.column.actions') }}</span>
              </nldd-text-cell>
            </nldd-table-row>
            <nldd-inline-dialog
              slot="empty"
              icon="key"
              :text="t('publish.access.keys.empty')"
              :supporting-text="t('publish.access.keys.emptyDetail')"
              data-testid="sleutels-leeg"
            ></nldd-inline-dialog>
            <nldd-table-row
              v-for="key in keys"
              :key="key.selector"
              :data-testid="`sleutel-${key.selector}`"
            >
              <nldd-text-cell
                :text="key.label"
                :supporting-text="key.selector"
              ></nldd-text-cell>
              <nldd-text-cell
                :text="formatDate(key.createdAt)"
                hide-below="md"
              ></nldd-text-cell>
              <nldd-text-cell
                :text="key.expiresAt ? formatDate(key.expiresAt) : t('publish.access.keys.never')"
                hide-below="md"
              ></nldd-text-cell>
              <nldd-cell hide-below="md">
                <nldd-tag
                  :color="key.status === 'active' ? 'success' : 'neutral'"
                  :text="
                    key.status === 'active'
                      ? t('publish.access.keys.active')
                      : t('publish.access.keys.revoked')
                  "
                ></nldd-tag>
              </nldd-cell>
              <nldd-cell horizontal-alignment="right">
                <RowActions :label="key.label" :actions="keyActions(key)" />
              </nldd-cell>
            </nldd-table-row>
          </nldd-table>

          <!-- A box, not just spacing: nldd-box draws its own surface, which is
               what says at a glance that these controls belong together and are
               not one more row of the table above. -->
          <nldd-box>
            <nldd-container layout="stack" padding="16">
              <nldd-form data-testid="sleutel-formulier" @submit.prevent="createKey">
                <nldd-form-section
                  :text="t('publish.access.keys.form.heading')"
                  :supporting-text="t('publish.access.keys.form.hint')"
                >
                  <nldd-form-field :label="t('publish.access.keys.form.label')" optional>
                    <nldd-text-field
                      name="sleutel-label"
                      :value="keyLabel"
                      :invalid="keyError !== null || undefined"
                      :unmet="keyError !== null ? 'sleutel-server' : undefined"
                      data-testid="sleutel-label"
                      @input="keyLabel = inputValue($event)"
                    ></nldd-text-field>
                    <nldd-form-field-help-text>
                      {{ t('publish.access.keys.form.labelHelp') }}
                    </nldd-form-field-help-text>
                    <nldd-validation-list>
                      <nldd-validation-item id="sleutel-server">
                        {{ keyError }}
                      </nldd-validation-item>
                    </nldd-validation-list>
                  </nldd-form-field>
                  <nldd-form-field :label="t('publish.access.keys.form.expiry')">
                    <nldd-dropdown width="180px">
                      <select
                        :value="keyDays"
                        :aria-label="t('publish.access.keys.form.expiry')"
                        data-testid="sleutel-dagen"
                        @change="keyDays = ($event.target as HTMLSelectElement).value"
                      >
                        <option value="7">
                          {{ t('publish.access.keys.form.days', { days: 7 }) }}
                        </option>
                        <option value="30">
                          {{ t('publish.access.keys.form.days', { days: 30 }) }}
                        </option>
                        <option value="90">
                          {{ t('publish.access.keys.form.days', { days: 90 }) }}
                        </option>
                        <option value="365">
                          {{ t('publish.access.keys.form.days', { days: 365 }) }}
                        </option>
                      </select>
                    </nldd-dropdown>
                  </nldd-form-field>
                  <nldd-form-actions>
                    <nldd-button
                      variant="primary"
                      type="submit"
                      :text="t('publish.access.keys.form.submit')"
                      data-testid="sleutel-aanmaken"
                    ></nldd-button>
                  </nldd-form-actions>
                </nldd-form-section>
              </nldd-form>
            </nldd-container>
          </nldd-box>
        </nldd-container>
      </section>

      <section v-if="access?.invitees" aria-labelledby="kop-genodigden">
        <nldd-container layout="stack" gap="16">
          <nldd-title :size="4">
            <h2 id="kop-genodigden">{{ t('access.invitees') }}</h2>
            <span slot="subtitle">{{ t('publish.access.invitees.intro') }}</span>
          </nldd-title>

          <nldd-table
            class="toegang-tabel"
            :accessible-label="t('access.invitees')"
            data-testid="genodigden-lijst"
            :columns="INVITEE_COLUMNS"
            :sm-columns="INVITEE_COLUMNS_SM"
          >
            <nldd-table-row slot="header">
              <nldd-text-cell
                :text="`**${t('publish.access.invitees.column.email')}**`"
              ></nldd-text-cell>
              <nldd-text-cell
                :text="`**${t('publish.access.invitees.column.added')}**`"
                hide-below="md"
              ></nldd-text-cell>
              <nldd-text-cell horizontal-alignment="right">
                <span class="alleen-schermlezer">{{ t('publish.access.column.actions') }}</span>
              </nldd-text-cell>
            </nldd-table-row>
            <nldd-inline-dialog
              slot="empty"
              icon="person-2"
              :text="t('publish.access.invitees.empty')"
              :supporting-text="t('publish.access.invitees.emptyDetail')"
              data-testid="genodigden-leeg"
            ></nldd-inline-dialog>
            <nldd-table-row
              v-for="invitee in invitees"
              :key="invitee.identifier"
              :data-testid="`genodigde-${invitee.identifier}`"
            >
              <nldd-text-cell :text="invitee.identifier"></nldd-text-cell>
              <nldd-text-cell
                :text="formatDate(invitee.addedAt)"
                hide-below="md"
              ></nldd-text-cell>
              <nldd-cell horizontal-alignment="right">
                <RowActions
                  :label="invitee.identifier"
                  :actions="inviteeActions(invitee)"
                />
              </nldd-cell>
            </nldd-table-row>
          </nldd-table>

          <nldd-box>
            <nldd-container layout="stack" padding="16">
              <nldd-form data-testid="genodigde-formulier" @submit.prevent="addInvitee">
                <nldd-form-section
                  :text="t('publish.access.invitees.form.heading')"
                  :supporting-text="t('publish.access.invitees.form.hint')"
                >
                  <nldd-form-field :label="t('publish.access.invitees.form.email')">
                    <nldd-text-field
                      type="email"
                      name="genodigde-email"
                      width="20rem"
                      required
                      :value="inviteeEmail"
                      :invalid="inviteeEmpty || inviteeError !== null || undefined"
                      :unmet="inviteeError !== null ? 'genodigde-server' : undefined"
                      autocomplete="off"
                      data-testid="genodigde-email"
                      @input="inviteeEmail = inputValue($event)"
                    ></nldd-text-field>
                    <nldd-validation-list>
                      <nldd-validation-item id="genodigde-email-vereist" required>
                        {{ t('publish.access.invitees.form.required') }}
                      </nldd-validation-item>
                      <nldd-validation-item id="genodigde-server">
                        {{ inviteeError }}
                      </nldd-validation-item>
                    </nldd-validation-list>
                  </nldd-form-field>
                  <nldd-form-actions>
                    <nldd-button
                      variant="primary"
                      type="submit"
                      :text="t('publish.access.invitees.form.submit')"
                      data-testid="genodigde-toevoegen"
                    ></nldd-button>
                  </nldd-form-actions>
                </nldd-form-section>
              </nldd-form>
            </nldd-container>
          </nldd-box>
        </nldd-container>
      </section>

      <section aria-labelledby="kop-externe-bronnen">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="kop-externe-bronnen">{{ t('publish.access.external.heading') }}</h2>
            <span slot="subtitle">{{ t('publish.access.external.intro') }}</span>
          </nldd-title>
          <nldd-switch-field
            :label="t('publish.access.external.label')"
            :checked="externalSources || undefined"
            data-testid="externe-bronnen"
            @change="chooseExternalSources"
          ></nldd-switch-field>
        </nldd-container>
      </section>

      <section aria-labelledby="kop-afscherming">
        <nldd-container layout="stack" gap="8">
          <nldd-title :size="4">
            <h2 id="kop-afscherming">{{ t('publish.access.sandbox.heading') }}</h2>
            <span slot="subtitle">{{ t('publish.access.sandbox.intro') }}</span>
          </nldd-title>
          <nldd-switch-field
            :label="t('publish.access.sandbox.label')"
            :checked="sandbox || undefined"
            data-testid="afscherming"
            @change="chooseSandbox"
          ></nldd-switch-field>
        </nldd-container>
      </section>
    </nldd-container>
  </nldd-activity-indicator>
</template>

<style scoped>
/* Same cap as the member tables, see global.css. */
.toegang-tabel {
  max-width: var(--plak-table-max-width);
}
</style>
