<script setup lang="ts">
/**
 * Site members on the site page: the content of the Leden tab. The list
 * mixes two kinds of people, so it comes in two blocks.
 *
 * Collapsed at the top, everyone who reaches this site through the group:
 * their role is changed on the group, so these rows carry no action menu.
 * Open below it, everyone with a role on this site itself; that role is the
 * only thing these routes can change.
 *
 * A site role only ever widens: the widest of the group role and the site role
 * is what someone may here. Narrowing through a site role silently does
 * nothing, which is why every action that could be meant that way says what it
 * really leads to.
 *
 * The API calls arrive as callback props so this component can be tested on its
 * own without the real or mocked fetch layer; the tab owns the source of truth
 * for the member list and reacts to the emits.
 */
import { computed, ref } from 'vue';

import ConfirmModal from '@/components/ConfirmModal.vue';
import MemberAddForm from '@/components/MemberAddForm.vue';
import RowActions, { type RowAction } from '@/components/RowActions.vue';
import type { MemberSuggestion, Role, SiteMember } from '@/api/types';
import { useMemberAddForm } from '@/composables/memberAddForm';
import { groupPath } from '@/composables/slug';
import { NOTE_SEPARATOR, useStartingList } from '@/composables/suggestionField';
import { roleLabel, ROLES, ROLE_ICONS, siteRoleHint } from '@/format';
import { t } from '@/i18n';
import DisclosureBox from '@/components/DisclosureBox.vue';
import { useConfirm } from '@/composables/confirm';

const props = defineProps<{
  members: SiteMember[];
  /** Slug of the group, for the link to its own Leden tab. */
  group: string;
  /** Display name of the group, as the summary of the collapsed block names it. */
  groupName: string;
  add: (identifier: string, role: Role) => Promise<SiteMember>;
  remove: (memberId: string) => Promise<void>;
  setRole: (memberId: string, role: Role) => Promise<SiteMember>;
  search: (query: string) => Promise<MemberSuggestion[]>;
}>();

const emit = defineEmits<{
  added: [SiteMember];
  removed: [string];
  roleChanged: [SiteMember];
}>();

/** Same three columns as the group's Leden tab, so the two tabs line up. */
const COLUMNS = 'minmax(12rem, 1fr) 9rem 3rem';
/** Below 640 px the role drops out; name and menu remain. */
const COLUMNS_SM = 'minmax(0, 1fr) 3rem';
/**
 * The inherited block has no menu, and its role stays visible at every width:
 * the role is the only reason to open this block, so hiding it on a phone
 * would empty it of meaning.
 */
const COLUMNS_INHERITED = 'minmax(12rem, 1fr) 9rem';
/** 12rem plus 9rem does not fit on 320 px, and the table would scroll sideways. */
const COLUMNS_INHERITED_SM = 'minmax(0, 1fr) 9rem';

interface Row {
  /** Empty on a provisional row: the member id only exists once the add lands. */
  memberId: string;
  identifier: string;
  name: string;
  email: string;
  groupRole: Role | null;
  siteRole: Role | null;
  effectiveRole: Role;
}

/** What someone may here: the widest of the two roles, never the narrowest. */
function widest(groupRole: Role | null, siteRole: Role): Role {
  return groupRole !== null && ROLES.indexOf(groupRole) > ROLES.indexOf(siteRole)
    ? groupRole
    : siteRole;
}

/**
 * Whether this site role would change anything, or the group role stays wider.
 * The mirror of `widest(group_role, site_role)` in the backend's
 * access/roles.py: anything not wider than the group role leaves what someone
 * may do here exactly as it was.
 */
function widens(groupRole: Role | null, role: Role): boolean {
  return groupRole === null || ROLES.indexOf(role) > ROLES.indexOf(groupRole);
}

/** What someone keeps when their site role goes or gets narrower. */
function keepsViaGroup(role: Role): string {
  return t('admin.siteMembers.keepsViaGroup', { role: roleLabel(role).toLowerCase() });
}

/**
 * One row, as a row of facts: the name, the address it is known by, and what
 * this person already reaches this site with. Separated rather than welded
 * together, so the name is the first fact and carries nothing of its own.
 * Someone with a site role of their own is marked rather than held back,
 * because the refusal then says what is the matter, which beats a row that
 * cannot be clicked and does not say why. A groepslid is offered like anyone
 * else, with the role they already reach this site with, since a site role
 * only widens.
 *
 * A member whose SSO profile carries no name is known by the address alone,
 * and that one stands where the name would be, once.
 *
 * All of it in `text`, with `details` left empty; see GroupMembersManager for
 * what that cell does to a row on a screen of 320 px.
 */
function suggestionText(person: MemberSuggestion): string {
  const facts = person.name ? [person.name, person.email] : [person.email];
  if (person.alreadyMember) {
    facts.push(t('admin.siteMembers.suggestion.alreadyMember'));
  } else if (person.groupRole !== null) {
    facts.push(
      t('admin.siteMembers.suggestion.viaGroup', {
        role: roleLabel(person.groupRole).toLowerCase(),
      }),
    );
  }
  return facts.join(NOTE_SEPARATOR);
}

// Optimistic overlay on props.members: what has been added but not confirmed
// yet, and whose site role is on its way out.
const provisional = ref<Row[]>([]);
const losingSiteRole = ref<string[]>([]);

const rows = computed<Row[]>(() => [
  ...props.members.flatMap((member) => {
    const siteRole = losingSiteRole.value.includes(member.identifier) ? null : member.siteRole;
    // Without a site role the group role carries the row; without either there
    // is no row left, which is what removing a site role does to an outsider.
    const effectiveRole = siteRole === null ? member.groupRole : widest(member.groupRole, siteRole);
    if (effectiveRole === null) return [];
    return [
      {
        memberId: member.memberId,
        identifier: member.identifier,
        name: member.name || member.identifier,
        email: member.email,
        groupRole: member.groupRole,
        siteRole,
        effectiveRole,
      },
    ];
  }),
  ...provisional.value,
]);

const inherited = computed(() => rows.value.filter((row) => row.siteRole === null));
const own = computed(() => rows.value.filter((row) => row.siteRole !== null));

/**
 * The list before anything is typed: the members of this group who have no
 * site role of their own yet, which is the common case for this form. They
 * are exactly the rows of the block above, so this costs no second request.
 */
const groupSuggestions = computed<MemberSuggestion[]>(() =>
  inherited.value.map((row) => ({
    identifier: row.identifier,
    // A row without a name from the IdP carries the address as its name; a
    // suggestion says that with an empty name, so the address stands once.
    name: row.name === row.identifier ? '' : row.name,
    email: row.email,
    alreadyMember: false,
    groupRole: row.groupRole,
  })),
);

/** The group role of whoever stands in the field, as far as a list knows it. */
const pickedGroupRole = computed<Role | null>(() => {
  const identifier = newIdentifier.value.trim();
  if (identifier === '') return null;
  const listed =
    suggestions.value.find((person) => person.identifier === identifier) ??
    groupSuggestions.value.find((person) => person.identifier === identifier);
  if (listed) return listed.groupRole;
  return rows.value.find((row) => row.identifier === identifier)?.groupRole ?? null;
});

/**
 * What a site role narrower than the group role comes to, which is nothing.
 * The form accepts it, because the role stands on its own once the group role
 * goes, but it should not be discovered afterwards that nothing happened.
 */
const roleChangesNothing = computed(
  () => pickedGroupRole.value !== null && !widens(pickedGroupRole.value, newRole.value),
);


const inheritedSummary = computed(() => {
  const count = inherited.value.length;
  // A key per form rather than one sentence with a plural glued in: the two
  // languages do not put the count and the noun together the same way.
  const key =
    count === 1
      ? 'admin.siteMembers.inherited.summary.one'
      : 'admin.siteMembers.inherited.summary.many';
  return t(key, { count, group: props.groupName });
});

/**
 * The sentence around the link to the group, split on the placeholder so the
 * link keeps its own element while the sentence stays one message.
 */
const inheritedNote = computed(() => t('admin.siteMembers.inherited.note').split('{link}'));

const form = useMemberAddForm<SiteMember>({
  search: (text) => props.search(text),
  emptyText: {
    tooShort: 'admin.siteMembers.form.tooShort',
    searching: 'admin.siteMembers.form.searching',
    none: 'admin.siteMembers.form.noSuggestions',
  },
  addFailed: 'admin.siteMembers.addFailed',
  add: (identifier, role) => props.add(identifier, role),
  onAdded: (member) => emit('added', member),
  // Someone already on the list is either on it twice or moves up out of the
  // inherited block, and both need the server's answer.
  isListed: (identifier) => rows.value.some((row) => row.identifier === identifier),
  addProvisional: (identifier, role) => {
    provisional.value = [
      ...provisional.value,
      {
        memberId: '',
        identifier,
        name: identifier,
        email: identifier,
        groupRole: null,
        siteRole: role,
        effectiveRole: role,
      },
    ];
  },
  dropProvisional: (identifier) => {
    provisional.value = provisional.value.filter((row) => row.identifier !== identifier);
  },
  // The group as the starting point, the whole platform once someone types.
  starting: () => groupSuggestions.value,
});
const {
  newIdentifier,
  newRole,
  identifierField,
  suggestions,
  shownSuggestions,
  // Whether the list on screen is still the group it starts with. The open
  // menu covers the help text under the field, so while it hides that line the
  // list has to say for itself that there is more behind it than the group.
  tooShort: startingList,
  notices,
  notify,
  dismissNotice,
  reopen,
} = form;

// An empty menu opening on arrival would only read as a broken dropdown.
useStartingList(identifierField, () => shownSuggestions.value.length > 0);

const addLabels = computed(() => ({
  legend: t('admin.siteMembers.form.heading'),
  supportingText: t('admin.siteMembers.form.hint'),
  identifier: t('admin.siteMembers.form.identifier'),
  placeholder: t('admin.siteMembers.form.identifier.placeholder'),
  identifierHelp: t('admin.siteMembers.form.identifier.help'),
  required: t('admin.siteMembers.form.identifier.required'),
  role: t('admin.siteMembers.form.role'),
  submit: t('admin.siteMembers.form.submit'),
}));

/** `removing` is the row whose site role is up for removal, once the menu asked for it. */
const {
  target: removing,
  busy: removeBusy,
  ask: askRemove,
  cancel: cancelRemove,
  confirm: confirmRemove,
} = useConfirm<Row>((row) => onRemove(row));

/**
 * Every site role this member does not have, plus taking the site role away.
 * A role that is not wider than the group role changes nothing, so the menu
 * says what it does lead to rather than leaving that to be discovered.
 */
function actionsFor(row: Row): RowAction[] {
  return [
    // Short label plus an icon. The second line is kept only where it says
    // something the label cannot: that this choice would change nothing,
    // because the group already grants more. Explaining what a role means
    // belongs under the picker below, not in every row menu.
    ...ROLES.filter((role) => role !== row.siteRole).map((role) => ({
      text: t('admin.siteMembers.action.setRole', { role: roleLabel(role).toLowerCase() }),
      icon: ROLE_ICONS[role],
      details: widens(row.groupRole, role) ? undefined : keepsViaGroup(row.groupRole!),
      testid: `site-role-${row.identifier}-${role}`,
      run: () => void onRole(row, role),
    })),
    {
      text: t('admin.siteMembers.action.remove'),
      icon: 'trash',
      destructive: true,
      testid: `site-role-remove-${row.identifier}`,
      run: () => askRemove(row),
    },
  ];
}

/**
 * What removing costs this one person, which is the whole reason to ask: a
 * group role catches them at its own level, and without one there is nothing
 * left to reach this site with.
 */
const removeText = computed(() => {
  const row = removing.value;
  if (row === null) return '';
  return row.groupRole === null
    ? t('admin.siteMembers.confirm.remove.noAccess', { name: row.name })
    : t('admin.siteMembers.confirm.remove.keepsViaGroup', {
        name: row.name,
        role: roleLabel(row.groupRole).toLowerCase(),
      });
});

async function onRole(row: Row, role: Role): Promise<void> {
  try {
    emit('roleChanged', await props.setRole(row.memberId, role));
  } catch (error) {
    notify(t('admin.siteMembers.roleFailed', { name: row.name }), error);
  }
}

async function onRemove(row: Row): Promise<void> {
  losingSiteRole.value = [...losingSiteRole.value, row.identifier];
  try {
    await props.remove(row.memberId);
    emit('removed', row.identifier);
  } catch (error) {
    notify(t('admin.siteMembers.removeFailed', { name: row.name }), error);
  } finally {
    losingSiteRole.value = losingSiteRole.value.filter(
      (identifier) => identifier !== row.identifier,
    );
  }
}
</script>

<template>
  <nldd-container layout="stack" gap="24">
    <DisclosureBox
      v-if="inherited.length > 0"
      :summary="inheritedSummary"
      summary-testid="members-via-group-summary"
      data-testid="members-via-group"
    >
      <nldd-container layout="stack" gap="8">
        <nldd-text size="sm">
          {{ inheritedNote[0]
          }}<nldd-link
            :href="groupPath(group, '/-/members')"
            size="inherit"
            data-testid="members-to-group"
          >{{ t('admin.siteMembers.inherited.link', { group: groupName }) }}</nldd-link
          >{{ inheritedNote[1] }}
        </nldd-text>

        <nldd-table
          class="member-table"
          :accessible-label="t('admin.siteMembers.inherited.table')"
          data-testid="members-via-group-list"
          :columns="COLUMNS_INHERITED"
          :sm-columns="COLUMNS_INHERITED_SM"
        >
          <nldd-table-row slot="header">
            <nldd-text-cell
              :text="`**${t('admin.column.member')}**`"
            ></nldd-text-cell>
            <nldd-text-cell
              :text="`**${t('admin.column.role')}**`"
            ></nldd-text-cell>
          </nldd-table-row>
          <nldd-table-row v-for="row in inherited" :key="row.identifier">
            <nldd-text-cell :text="row.name" :supporting-text="row.email"></nldd-text-cell>
            <nldd-text-cell
              :text="roleLabel(row.effectiveRole)"
            ></nldd-text-cell>
          </nldd-table-row>
        </nldd-table>
      </nldd-container>
    </DisclosureBox>

    <nldd-container layout="stack" gap="16">
      <nldd-table
        class="member-table"
        :accessible-label="t('admin.siteMembers.table')"
        data-testid="members-list"
        :columns="COLUMNS"
        :sm-columns="COLUMNS_SM"
      >
        <nldd-table-row slot="header">
          <nldd-text-cell
            :text="`**${t('admin.column.member')}**`"
          ></nldd-text-cell>
          <nldd-text-cell
            :text="`**${t('admin.column.role')}**`"
            hide-below="md"
          ></nldd-text-cell>
          <!-- Named but not shown, as on the group's Leden tab: a word above
               one icon button says nothing, and a columnheader without a name
               is an axe violation. -->
          <nldd-text-cell horizontal-alignment="right">
            <span class="visually-hidden">{{ t('admin.column.actions') }}</span>
          </nldd-text-cell>
        </nldd-table-row>
        <nldd-inline-dialog
          slot="empty"
          icon="person-2"
          :text="t('admin.siteMembers.empty')"
          :supporting-text="t('admin.siteMembers.empty.detail')"
        ></nldd-inline-dialog>
        <nldd-table-row v-for="row in own" :key="row.identifier">
          <nldd-text-cell :text="row.name" :supporting-text="row.email"></nldd-text-cell>
          <nldd-text-cell
            :text="roleLabel(row.effectiveRole)"
            :supporting-text="
              row.effectiveRole === row.siteRole ? undefined : t('admin.siteMembers.viaGroup')
            "
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
        :role-hint="siteRoleHint(newRole)"
        :suggestion-text="suggestionText"
        testid-prefix="site-role"
        role-testid="site-role-new"
      >
        <template #menu-footer>
          <!-- The footer sits outside role="menu", so it is neither a
               choosable option nor a stop for arrow keys; it holds no
               control, so it is no tab stop either. The slot is unpadded,
               hence the container. -->
          <nldd-container v-if="startingList" slot="footer" padding="8">
            <nldd-text size="sm" data-testid="site-role-further-search">
              {{ t('admin.siteMembers.form.searchFurther') }}
            </nldd-text>
          </nldd-container>
        </template>
        <template #role-help>
          <span v-if="roleChangesNothing" data-testid="site-role-no-effect">{{
            t('admin.siteMembers.form.role.noEffect', {
              role: roleLabel(pickedGroupRole!).toLowerCase(),
            })
          }}</span>
        </template>
      </MemberAddForm>
    </nldd-container>
  </nldd-container>

  <ConfirmModal
    :open="removing !== null"
    :title="t('admin.siteMembers.confirm.remove.title', { name: removing?.name ?? '' })"
    :text="removeText"
    :keep-label="t('admin.siteMembers.confirm.remove.keep')"
    :confirm-label="t('admin.siteMembers.confirm.remove.confirm')"
    :busy="removeBusy"
    @confirm="confirmRemove"
    @close="cancelRemove"
  />

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
      :text="t('admin.retry')"
      @click="reopen(notice)"
    ></nldd-button>
  </nldd-notification>
</template>

<style scoped>
/* Same cap as the group's member table, see global.css. */
.member-table {
  max-width: var(--plak-table-max-width);
}
</style>
