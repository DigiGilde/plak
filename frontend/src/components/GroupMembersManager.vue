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
import MemberAddForm from '@/components/MemberAddForm.vue';
import RowActions, { type RowAction } from '@/components/RowActions.vue';
import type { GroupMember, GroupSiteRole, MemberSuggestion, Role } from '@/api/types';
import { useMemberAddForm } from '@/composables/memberAddForm';
import { NOTE_SEPARATOR } from '@/composables/suggestionField';
import { roleHint, roleLabel, ROLES, ROLE_ICONS } from '@/format';
import { t } from '@/i18n';
import { useConfirm } from '@/composables/confirm';

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

const form = useMemberAddForm<GroupMember>({
  search: (text) => props.search(text),
  emptyText: {
    tooShort: 'group.members.add.suggestions.tooShort',
    searching: 'group.members.add.suggestions.searching',
    none: 'group.members.add.suggestions.empty',
  },
  addFailed: 'group.members.addFailed',
  add: (identifier, role) => props.add(identifier, role),
  onAdded: (member) => emit('added', member),
  isListed: (identifier) => rows.value.some((row) => row.identifier === identifier),
  addProvisional: (identifier, role) => {
    provisional.value = [
      ...provisional.value,
      { memberId: '', identifier, name: identifier, email: identifier, role, siteRoles: [] },
    ];
  },
  dropProvisional: (identifier) => {
    provisional.value = provisional.value.filter((row) => row.identifier !== identifier);
  },
});
const { notices, notify, dismissNotice, reopen, newRole } = form;

const addLabels = computed(() => ({
  legend: t('group.members.add.legend'),
  supportingText: t('group.members.add.supportingText'),
  identifier: t('group.members.add.identifier.label'),
  placeholder: t('group.members.add.identifier.placeholder'),
  identifierHelp: t('group.members.add.identifier.help'),
  required: t('group.members.add.identifier.required'),
  role: t('group.members.add.role.label'),
  submit: t('group.members.add.submit'),
}));

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

/**
 * Whether the site roles go along. Off on opening, every time: taking more
 * than was asked is a destructive step and has to be chosen, not inherited
 * from the last person who was removed.
 */
const alsoSiteRoles = ref(false);

/** `removing` is the row whose group membership is up for removal, once the menu asked. */
const {
  target: removing,
  busy: removeBusy,
  ask,
  cancel: cancelRemove,
  confirm: confirmRemove,
} = useConfirm<Row>((row) => onRemove(row, alsoSiteRoles.value ? 'remove' : 'keep'));

function askRemove(row: Row): void {
  alsoSiteRoles.value = false;
  ask(row);
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

    <MemberAddForm
      :form="form"
      :labels="addLabels"
      :role-hint="roleHint(newRole)"
      :suggestion-text="suggestionText"
      testid-prefix="member"
      role-testid="member-role-new"
    />
  </nldd-container>

  <ConfirmModal
    :open="removing !== null"
    :title="t('group.members.confirm.remove.title', { name: removing?.name ?? '' })"
    :text="removeText"
    :keep-label="t('group.members.confirm.remove.keep')"
    :confirm-label="removeConfirmLabel"
    :busy="removeBusy"
    @confirm="confirmRemove"
    @close="cancelRemove"
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
