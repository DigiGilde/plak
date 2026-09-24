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

import RowActions, { type RowAction } from '@/components/RowActions.vue';
import type { GroupMember, MemberSuggestion, Role } from '@/api/types';
import { looksLikeEmail, useMemberSearch } from '@/composables/memberSearch';
import { useNotices, type Notice } from '@/composables/notices';
import { roleHint, roleLabel, ROLES, ROLE_ICONS } from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  members: GroupMember[];
  add: (identifier: string, role: Role) => Promise<GroupMember>;
  remove: (memberId: string) => Promise<void>;
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

interface Row {
  /** Empty on a provisional row: the member id only exists once the add lands. */
  memberId: string;
  identifier: string;
  name: string;
  email: string;
  role: Role;
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
/**
 * Set on submit when the field holds neither a picked suggestion nor a typed
 * e-mail address: a typed name that was never picked cannot be resolved on
 * this side, and sending it would only come back as a 404. Picking a
 * suggestion always puts an e-mail address in the field (`identifier` on a
 * `MemberSuggestion` is the person's e-mail), so this never fires for a pick.
 */
const notAnEmail = ref(false);

const { suggestions, query: searchFor, clear: clearSuggestions } = useMemberSearch((text) =>
  props.search(text),
);

/** Someone who is already in the group stays in the list, marked and inert. */
function suggestionText(person: MemberSuggestion): string {
  const label = person.name || person.email;
  return person.alreadyMember
    ? t('group.members.suggestion.alreadyMember', { name: label })
    : label;
}

/**
 * The address on the right of the row, next to the name. A member whose SSO
 * profile carries no name is known by the address alone, and that one already
 * stands where the name would be.
 */
function suggestionDetails(person: MemberSuggestion): string | undefined {
  return person.name ? person.email : undefined;
}

function onIdentifierInput(event: CustomEvent<{ value?: string }>): void {
  const typed = event.detail?.value ?? (event.target as HTMLInputElement).value;
  newIdentifier.value = typed;
  newLabel.value = typed;
  emptyField.value = false;
  notAnEmail.value = false;
  searchFor(typed);
}

/**
 * A suggestion carries the name as its label and the address as its value, so
 * picking one submits the address while the field keeps showing the name. On a
 * typed address that no suggestion matches, both are that address.
 */
function onIdentifierChange(event: CustomEvent<{ value?: string }>): void {
  const identifier = event.detail?.value ?? '';
  newIdentifier.value = identifier;
  newLabel.value =
    suggestions.value.find((person) => person.identifier === identifier)?.name || identifier;
  emptyField.value = false;
  notAnEmail.value = false;
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
    })),
  ...provisional.value,
]);

const { notices, notify, dismissNotice } = useNotices();

function reopen(notice: Notice): void {
  newIdentifier.value = notice.retry;
  newLabel.value = notice.retry;
  emptyField.value = false;
  notAnEmail.value = false;
  dismissNotice(notice.id);
}

async function onAdd(): Promise<void> {
  const identifier = newIdentifier.value.trim();
  emptyField.value = identifier === '';
  if (emptyField.value) return;
  // A picked suggestion always leaves an e-mail address in the field; a typed
  // name that was never picked would only come back from the server as a 404.
  notAnEmail.value = !looksLikeEmail(identifier);
  if (notAnEmail.value) return;

  newIdentifier.value = '';
  newLabel.value = '';
  clearSuggestions();
  // If the address is already in the list there is nothing to run ahead of: a
  // second row would get the same :key. The server reports the duplicate.
  const newRow = !rows.value.some((row) => row.identifier === identifier);
  if (newRow) {
    provisional.value = [
      ...provisional.value,
      { memberId: '', identifier, name: identifier, email: identifier, role: newRole.value },
    ];
  }
  try {
    const member = await props.add(identifier, newRole.value);
    emit('added', member);
  } catch (error) {
    notify(t('group.members.addFailed', { identifier }), error, identifier);
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
      testid: `lid-rol-${row.identifier}-${role}`,
      run: () => void onRole(row, role),
    })),
    {
      text: t('group.members.action.remove'),
      icon: 'trash',
      destructive: true,
      testid: `lid-verwijderen-${row.identifier}`,
      run: () => void onRemove(row),
    },
  ];
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

async function onRemove(row: Row): Promise<void> {
  hidden.value = [...hidden.value, row.identifier];
  try {
    await props.remove(row.memberId);
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
      data-testid="leden-lijst"
      :columns="COLUMNS"
      :sm-columns="COLUMNS_SM"
    >
      <nldd-table-row slot="header">
        <nldd-text-cell
          size="sm"
          color="secondary"
          :text="t('group.members.column.member')"
        ></nldd-text-cell>
        <nldd-text-cell
          size="sm"
          color="secondary"
          :text="t('group.members.column.role')"
          hide-below="md"
        ></nldd-text-cell>
        <!-- Named but not shown, as on Platformbeheer: a word above one icon
             button says nothing, and a columnheader without a name is an axe
             violation. -->
        <nldd-text-cell size="sm" color="secondary" horizontal-alignment="right">
          <span class="alleen-schermlezer">{{ t('group.members.column.actions') }}</span>
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
          size="sm"
          color="secondary"
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
        <nldd-form data-testid="lid-formulier" @submit.prevent="onAdd">
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
          <!-- A combo box with allow-custom, not a picker: typing a name is the
               short road, but the list only holds people who have logged in on
               the beheer, and an address that is not in it still has to be
               submittable. -->
        <nldd-form-field :label="t('group.members.add.identifier.label')">
          <nldd-combo-box
            name="identifier"
            allow-custom
            :placeholder="t('group.members.add.identifier.placeholder')"
            required
            :value="newIdentifier"
            :text="newLabel"
            :invalid="emptyField || notAnEmail || undefined"
            :unmet="notAnEmail ? 'lid-toevoegen-geen-email' : undefined"
            @input="onIdentifierInput"
            @change="onIdentifierChange"
          >
            <nldd-menu
              :empty-text="t('group.members.add.suggestions.empty')"
              data-testid="lid-suggesties"
            >
              <nldd-menu-item
                v-for="person in suggestions"
                :key="person.identifier"
                :text="suggestionText(person)"
                :value="person.identifier"
                :details="suggestionDetails(person)"
                :disabled="person.alreadyMember || undefined"
                :data-testid="`lid-suggestie-${person.identifier}`"
              ></nldd-menu-item>
            </nldd-menu>
          </nldd-combo-box>
          <nldd-form-field-help-text>
            {{ t('group.members.add.identifier.help') }}
          </nldd-form-field-help-text>
          <nldd-validation-list>
            <nldd-validation-item id="lid-toevoegen-vereist" required>
              {{ t('group.members.add.identifier.required') }}
            </nldd-validation-item>
            <!-- No rule of its own: named in the combo box's `unmet` attribute
                 instead, since it depends on whether the typed text is a pick
                 or a plain e-mail address, not on a pattern the field can
                 check by itself. -->
            <nldd-validation-item id="lid-toevoegen-geen-email">
              {{ t('group.members.add.identifier.notAnEmail') }}
            </nldd-validation-item>
          </nldd-validation-list>
        </nldd-form-field>

          <!-- The role is picked while adding, not afterwards: it decides what
               someone may from their first minute, and lezer as the default makes
               promoting the deliberate step. -->
          <nldd-form-field :label="t('group.members.add.role.label')">
          <nldd-dropdown>
            <select v-model="newRole" name="rol" data-testid="lid-rol-nieuw">
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
