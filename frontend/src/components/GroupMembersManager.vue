<script setup lang="ts">
/**
 * Group members on the group page. Adding and removing happen in the table
 * itself: this is the content of the Leden tab, so there is no editing
 * task to open a side panel for. Both operations are optimistic: the row
 * appears or disappears straight away, the API confirms in the background, and
 * on failure the component rolls back with an `nldd-notification`.
 *
 * The API calls arrive as callback props so this component can be tested on its
 * own without the real or mocked fetch layer; the page owns the source of truth
 * for the member list and reacts to the emits.
 */
import { computed, ref } from 'vue';

import ConfirmModal from '@/components/ConfirmModal.vue';
import RowActions, { type RowAction } from '@/components/RowActions.vue';
import type { GroupMember, GroupSiteRole, MemberSuggestion, Role } from '@/api/types';
import { SEARCH_MIN_LENGTH, useMemberSearch } from '@/composables/memberSearch';
import { useNotices, type Notice } from '@/composables/notices';
import {
  keepEverySuggestion,
  NOTE_SEPARATOR,
  useMenuEmptyState,
} from '@/composables/suggestionField';
import { roleHint, roleLabel, ROLES, ROLE_ICONS } from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  members: GroupMember[];
  add: (identifier: string, role: Role) => Promise<GroupMember>;
  remove: (memberId: string, siteRoles: 'keep' | 'remove') => Promise<void>;
  setRole: (memberId: string, role: Role) => Promise<GroupMember>;
  search: (query: string) => Promise<MemberSuggestion[]>;
}>();

const emit = defineEmits<{
  added: [GroupMember];
  removed: [string];
  roleChanged: [GroupMember];
}>();

/**
 * The columns sit on the table once, so name, role and menu line up on the
 * same x across all rows. Same shape as Platformbeheer: the name stretches,
 * the role is as wide as "Redacteur" needs, and the menu is one icon button.
 * The email address has no column of its own; it sits under the name,
 * which is where you read it anyway and which leaves the role somewhere to be.
 */
const COLUMNS = 'minmax(12rem, 1fr) 9rem 3rem';
/** Below 640 px the role drops out; name and menu remain. */
const COLUMNS_SM = 'minmax(0, 1fr) 3rem';

/**
 * How many sites the confirmation names before it counts the rest. A list of
 * three reads; a list of thirty pushes the buttons off a phone screen and
 * turns the question into a scroll. Five names is enough to recognise what
 * this is about, and the tail is a number.
 */
const SITES_NAMED = 5;

interface Row {
  /** Empty on a provisional row: the member id only exists once the add lands. */
  memberId: string;
  identifier: string;
  name: string;
  email: string;
  role: Role;
  /** Only sites in this group; the API scopes it and so does this screen. */
  siteRoles: GroupSiteRole[];
}

/** What gets submitted: an e-mail address, typed or picked from the list. */
const newIdentifier = ref('');
/**
 * What the field shows, which is the name once a suggestion is picked. Bound
 * alongside the value so the combo box never derives a label of its own: it
 * would rewrite what someone is still typing the moment it happens to match
 * an address in the list.
 */
const newLabel = ref('');
/**
 * Lezer by default: starting narrow makes promoting to full power over the
 * group a deliberate step, instead of having to demote from it.
 */
const newRole = ref<Role>('reader');
const emptyField = ref(false);

const {
  suggestions,
  searching,
  query: searchFor,
  clear: clearSuggestions,
} = useMemberSearch((text) => props.search(text));

/**
 * The menu opens on the first keystroke, long before an answer is in, so its
 * empty state has to tell three situations apart rather than call all three
 * "niemand gevonden".
 */
const emptyText = computed(() => {
  if (newLabel.value.trim().length < SEARCH_MIN_LENGTH) {
    return t('group.members.add.suggestions.tooShort');
  }
  return searching.value
    ? t('group.members.add.suggestions.searching')
    : t('group.members.add.suggestions.empty');
});

const identifierField = ref<HTMLElement | null>(null);

// The rows arrive after the menu has already decided that it is empty.
useMenuEmptyState(identifierField, () => suggestions.value);

/**
 * One row, as a row of facts: the name, the address it is known by, and
 * whether this person is already in the group. Separated rather than welded
 * together, so the name is the first fact and carries nothing of its own. A
 * member already in the group is marked rather than held back, because the
 * refusal then says what is the matter, which beats a row that cannot be
 * clicked and does not say why.
 *
 * A member whose SSO profile carries no name is known by the address alone,
 * and that one stands where the name would be, once.
 *
 * All of it in `text`, with `details` left empty. `nldd-menu-item` renders
 * `details` in a cell of `width: fit-content`, which neither shrinks nor
 * truncates; on 320 px it keeps its full width and leaves the name too little
 * to break a word in, which reflows into one character per line. The text
 * cell wraps like prose. Measured on 320 px with three rows: 548 px for the
 * tallest row with the address in `details`, 188 px with all of it here.
 */
function suggestionText(person: MemberSuggestion): string {
  const facts = person.name ? [person.name, person.email] : [person.email];
  if (person.alreadyMember) facts.push(t('group.members.suggestion.alreadyMember'));
  return facts.join(NOTE_SEPARATOR);
}

function onIdentifierInput(event: CustomEvent<{ value?: string }>): void {
  // The component reports what stands in the input as `detail.value`. The
  // native input event of its own inner field is composed and bubbles out
  // here as well, carrying no detail; that one is the same keystroke twice.
  const typed = event.detail?.value;
  if (typeof typed !== 'string') return;
  // Only a pick is an identifier: typing on takes back the one before it.
  newIdentifier.value = '';
  newLabel.value = typed;
  emptyField.value = false;
  searchFor(typed);
}

/**
 * A suggestion carries the name as its label and the address as its value, so
 * picking one submits the address while the field keeps showing the name.
 */
function onIdentifierChange(event: CustomEvent<{ value?: string }>): void {
  // Same double event as on input: the inner field's own change bubbles out
  // here too, without a detail, and would wipe the pick it follows.
  const identifier = event.detail?.value;
  if (typeof identifier !== 'string') return;
  newIdentifier.value = identifier;
  newLabel.value =
    suggestions.value.find((person) => person.identifier === identifier)?.name || identifier;
  emptyField.value = false;
}

// Optimistic overlay on props.members: what has been added but not confirmed
// yet, and what has been removed but not confirmed yet.
const provisional = ref<Row[]>([]);
const hidden = ref<string[]>([]);

const rows = computed<Row[]>(() => [
  ...props.members
    .filter((member) => !hidden.value.includes(member.identifier))
    // Without a name from the IdP the identifier carries the row, just as for
    // a member added a moment ago; otherwise the row changes on confirmation.
    .map((member) => ({
      memberId: member.memberId,
      identifier: member.identifier,
      name: member.name || member.identifier,
      email: member.email,
      role: member.role,
      siteRoles: member.siteRoles,
    })),
  ...provisional.value,
]);

const { notices, notify, dismissNotice } = useNotices();

function reopen(notice: Notice): void {
  newIdentifier.value = notice.retry;
  newLabel.value = notice.retry;
  emptyField.value = false;
  dismissNotice(notice.id);
}

async function onAdd(): Promise<void> {
  const identifier = newIdentifier.value.trim();
  emptyField.value = identifier === '';
  if (emptyField.value) return;
  // The field only submits what was picked, so the label beside the address is
  // the name of whoever was picked. Read before the field is emptied.
  /* v8 ignore start -- newLabel is always set together with newIdentifier (by
   * onIdentifierChange or reopen()), to the picked name or the identifier
   * itself, so it is never blank once emptyField has let this line run. */
  const name = newLabel.value.trim() || identifier;
  /* v8 ignore stop */

  newIdentifier.value = '';
  newLabel.value = '';
  clearSuggestions();
  // If the address is already in the list there is nothing to run ahead of: a
  // second row would get the same :key. The server reports the duplicate.
  const newRow = !rows.value.some((row) => row.identifier === identifier);
  if (newRow) {
    provisional.value = [
      ...provisional.value,
      {
        memberId: '',
        identifier,
        name: identifier,
        email: identifier,
        role: newRole.value,
        siteRoles: [],
      },
    ];
  }
  try {
    const member = await props.add(identifier, newRole.value);
    emit('added', member);
  } catch (error) {
    notify(t('group.members.addFailed', { name }), error, identifier);
  } finally {
    if (newRow) {
      provisional.value = provisional.value.filter((row) => row.identifier !== identifier);
    }
  }
}

/**
 * Every role but the one this member already has, so the menu offers changes.
 *
 * Short label plus an icon, without the line explaining what the role means:
 * whoever opens a row menu has already read that under the picker below, and
 * three sentences in a menu bury the one thing you came for.
 */
function actionsFor(row: Row): RowAction[] {
  return [
    ...ROLES.filter((role) => role !== row.role).map((role) => ({
      text: t('group.members.action.setRole', { role: roleLabel(role).toLowerCase() }),
      icon: ROLE_ICONS[role],
      testid: `member-role-${row.identifier}-${role}`,
      run: () => void onRole(row, role),
    })),
    {
      text: t('group.members.action.remove'),
      icon: 'trash',
      destructive: true,
      testid: `member-delete-${row.identifier}`,
      run: () => askRemove(row),
    },
  ];
}

/** The row whose group membership is up for removal, once the menu asked. */
const removing = ref<Row | null>(null);
const removeBusy = ref(false);
/**
 * Whether the site roles go along. Off on opening, every time: taking more
 * than was asked is a destructive step and has to be chosen, not inherited
 * from the last person who was removed.
 */
const alsoSiteRoles = ref(false);

function askRemove(row: Row): void {
  alsoSiteRoles.value = false;
  removing.value = row;
}

/** The site roles this person holds in this group, the named ones first. */
const removeSiteRoles = computed<GroupSiteRole[]>(() => removing.value?.siteRoles ?? []);
// Only read where removeSiteRoles.length > 0, which is only true while
// removing is set, so the row is guaranteed to be there.
const removingName = computed(() => removing.value!.name);
const namedSites = computed(() => removeSiteRoles.value.slice(0, SITES_NAMED));
const unnamedSites = computed(() => removeSiteRoles.value.length - namedSites.value.length);

const unnamedText = computed(() =>
  t(
    unnamedSites.value === 1
      ? 'group.members.confirm.remove.siteRoles.more.one'
      : 'group.members.confirm.remove.siteRoles.more.many',
    { count: unnamedSites.value },
  ),
);

/**
 * What it costs, which is the whole reason to ask: a group role reaches every
 * site in the group at once. The second sentence states what is true of this
 * person, since the row now carries the site roles they hold here.
 */
const removeText = computed(() => {
  const row = removing.value;
  if (row === null) return '';
  const first = t('group.members.confirm.remove.text', {
    name: row.name,
    role: roleLabel(row.role).toLowerCase(),
  });
  const count = row.siteRoles.length;
  if (count === 0) {
    return `${first} ${t('group.members.confirm.remove.noSiteRoles', { name: row.name })}`;
  }
  const key =
    count === 1
      ? 'group.members.confirm.remove.siteRoles.one'
      : 'group.members.confirm.remove.siteRoles.many';
  return `${first} ${t(key, { name: row.name, count })}`;
});

// The button names what will happen, which is not the same thing twice: with
// the box ticked more goes than the menu item asked for.
const removeConfirmLabel = computed(() =>
  t(
    alsoSiteRoles.value
      ? 'group.members.confirm.remove.confirmWithSiteRoles'
      : 'group.members.confirm.remove.confirm',
  ),
);

function toggleSiteRoles(event: CustomEvent<{ checked?: boolean }>): void {
  // Same double event as on the combo box above: the inner control's own
  // change bubbles out here too, without a detail, and would read as unticked.
  const checked = event.detail?.checked;
  if (typeof checked !== 'boolean') return;
  alsoSiteRoles.value = checked;
}

async function confirmRemove(): Promise<void> {
  const row = removing.value;
  /* v8 ignore start -- the modal only confirms while it is open, so there is a row. */
  if (row === null) return;
  /* v8 ignore stop */
  removeBusy.value = true;
  try {
    await onRemove(row, alsoSiteRoles.value ? 'remove' : 'keep');
  } finally {
    removeBusy.value = false;
    removing.value = null;
  }
}

async function onRole(row: Row, role: Role): Promise<void> {
  try {
    emit('roleChanged', await props.setRole(row.memberId, role));
  } catch (error) {
    // The backend refuses a change that would leave the group without a
    // beheerder; its message says so.
    notify(t('group.members.roleChangeFailed', { name: row.name }), error);
  }
}

async function onRemove(row: Row, siteRoles: 'keep' | 'remove'): Promise<void> {
  hidden.value = [...hidden.value, row.identifier];
  try {
    await props.remove(row.memberId, siteRoles);
    emit('removed', row.identifier);
  } catch (error) {
    notify(t('group.members.removeFailed', { name: row.name }), error);
  } finally {
    hidden.value = hidden.value.filter((identifier) => identifier !== row.identifier);
  }
}
</script>

<template>
  <nldd-container layout="stack" gap="16">
    <nldd-table
      class="member-table"
      :accessible-label="t('group.members.table.label')"
      data-testid="members-list"
      :columns="COLUMNS"
      :sm-columns="COLUMNS_SM"
    >
      <nldd-table-row slot="header">
        <nldd-text-cell
          :text="`**${t('group.members.column.member')}**`"
        ></nldd-text-cell>
        <nldd-text-cell
          :text="`**${t('group.members.column.role')}**`"
          hide-below="md"
        ></nldd-text-cell>
        <!-- Named but not shown, as on Platformbeheer: a word above one icon
             button says nothing, and a columnheader without a name is an axe
             violation. -->
        <nldd-text-cell horizontal-alignment="right">
          <span class="visually-hidden">{{ t('group.members.column.actions') }}</span>
        </nldd-text-cell>
      </nldd-table-row>
      <nldd-inline-dialog
        slot="empty"
        icon="person-2"
        :text="t('group.members.empty')"
        :supporting-text="t('group.members.empty.supportingText')"
      ></nldd-inline-dialog>
      <nldd-table-row v-for="row in rows" :key="row.identifier">
        <nldd-text-cell :text="row.name" :supporting-text="row.email"></nldd-text-cell>
        <nldd-text-cell
          :text="roleLabel(row.role)"
          hide-below="md"
        ></nldd-text-cell>
        <nldd-cell horizontal-alignment="right">
          <RowActions :label="row.name" :actions="actionsFor(row)" />
        </nldd-cell>
      </nldd-table-row>
    </nldd-table>

    <!-- A box, not just spacing: nldd-box draws its own surface, which is
         what says at a glance that these controls belong together and are
         not one more row of the table above. -->
    <nldd-box>
      <nldd-container layout="stack" padding="16">
        <nldd-form data-testid="member-form" @submit.prevent="onAdd">
        <!-- A real fieldset with a legend, which is what nldd-form-section
             renders in the light DOM. Without it the two fields and the button
             float under the table as three unrelated things, and a screen reader
             announces them without ever saying what they are for. -->
        <nldd-form-section
          :text="t('group.members.add.legend')"
          :supporting-text="t('group.members.add.supportingText')"
        >
          <!-- Who comes first, what they may second: the role only makes
               sense once you know whom it is for. -->
          <!-- A combo box without allow-custom: typing searches, but only a
               name from the list can be submitted. An address that is not in
               it belongs to nobody who has ever logged in on the admin, and
               the server would refuse it. -->
        <nldd-form-field :label="t('group.members.add.identifier.label')">
          <nldd-combo-box
            ref="identifierField"
            name="identifier"
            :placeholder="t('group.members.add.identifier.placeholder')"
            required
            :value="newIdentifier"
            :text="newLabel"
            :invalid="emptyField || undefined"
            @input="onIdentifierInput"
            @change="onIdentifierChange"
          >
            <nldd-menu
              :empty-text="emptyText"
              :filterFn.prop="keepEverySuggestion"
              data-testid="member-suggestions"
            >
              <nldd-menu-item
                v-for="person in suggestions"
                :key="person.identifier"
                :text="suggestionText(person)"
                :value="person.identifier"
                :data-testid="`member-suggestion-${person.identifier}`"
              ></nldd-menu-item>
            </nldd-menu>
          </nldd-combo-box>
          <nldd-form-field-help-text>
            {{ t('group.members.add.identifier.help') }}
          </nldd-form-field-help-text>
          <!-- The value to check, handed over rather than read off the
               control: the list re-checks on the control's `input` event, and
               picking from the menu is a `change` without one. -->
          <nldd-validation-list :value="newIdentifier">
            <nldd-validation-item id="member-add-required" required>
              {{ t('group.members.add.identifier.required') }}
            </nldd-validation-item>
          </nldd-validation-list>
        </nldd-form-field>

          <!-- The role is picked while adding, not afterwards: it decides what
               someone may from their first minute, and lezer as the default makes
               promoting the deliberate step. -->
          <nldd-form-field :label="t('group.members.add.role.label')">
          <nldd-dropdown>
            <select v-model="newRole" name="role" data-testid="member-role-new">
              <option v-for="role in ROLES" :key="role" :value="role">
                {{ roleLabel(role) }}
              </option>
            </select>
          </nldd-dropdown>
          <nldd-form-field-help-text>{{ roleHint(newRole) }}</nldd-form-field-help-text>
        </nldd-form-field>

          <nldd-form-actions>
            <nldd-button
              variant="primary"
              :text="t('group.members.add.submit')"
              type="submit"
            ></nldd-button>
          </nldd-form-actions>
        </nldd-form-section>
        </nldd-form>
      </nldd-container>
    </nldd-box>
  </nldd-container>

  <ConfirmModal
    :open="removing !== null"
    :title="t('group.members.confirm.remove.title', { name: removing?.name ?? '' })"
    :text="removeText"
    :keep-label="t('group.members.confirm.remove.keep')"
    :confirm-label="removeConfirmLabel"
    :busy="removeBusy"
    @confirm="confirmRemove"
    @close="removing = null"
  >
    <nldd-container v-if="removeSiteRoles.length > 0" layout="stack" gap="16">
      <!-- type="form": rows that are not actions but facts, so the list does
           not promise a keyboard to walk through. -->
      <nldd-list
        type="form"
        dividers="never"
        :accessible-label="t('group.members.confirm.remove.siteRoles.list')"
        data-testid="site-roles-list"
      >
        <nldd-list-item v-for="site in namedSites" :key="site.siteSlug">
          <nldd-text-cell
            :text="site.siteTitle"
            :supporting-text="roleLabel(site.role)"
          ></nldd-text-cell>
        </nldd-list-item>
        <nldd-list-item v-if="unnamedSites > 0" data-testid="site-roles-rest">
          <nldd-text-cell size="sm" :text="unnamedText"></nldd-text-cell>
        </nldd-list-item>
      </nldd-list>

      <!-- Off on opening, and the sentence under it says what that leaves
           standing, so the safe choice is not the unexplained one. -->
      <nldd-checkbox-field
        :label="t('group.members.confirm.remove.siteRoles.also')"
        :checked="alsoSiteRoles || undefined"
        data-testid="site-roles-include"
        @change="toggleSiteRoles"
      ></nldd-checkbox-field>
      <nldd-rich-text>
        <p>
          {{
            t('group.members.confirm.remove.siteRoles.keeps', { name: removingName })
          }}
        </p>
      </nldd-rich-text>
    </nldd-container>
  </ConfirmModal>

  <nldd-notification
    v-for="notice in notices"
    :key="notice.id"
    variant="critical"
    :text="notice.text"
    :supporting-text="notice.detail"
    @dismiss="dismissNotice(notice.id)"
  >
    <nldd-button
      v-if="notice.retry"
      slot="actions"
      variant="secondary"
      size="sm"
      :text="t('group.members.retry')"
      @click="reopen(notice)"
    ></nldd-button>
  </nldd-notification>
</template>

<style scoped>
/* Measured in Chromium (root font 16 px): the three columns together need
   619 px at their widest content (a full name 241, a long email address 232,
   action 105, plus 2x8 px column gap and 2x12 px row padding). That is the
   number behind the shared cap in global.css. */
.member-table {
  max-width: var(--plak-table-max-width);
}
</style>
