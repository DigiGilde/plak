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
import RowActions, { type RowAction } from '@/components/RowActions.vue';
import type { MemberSuggestion, Role, SiteMember } from '@/api/types';
import { SEARCH_MIN_LENGTH, useMemberSearch } from '@/composables/memberSearch';
import { useNotices, type Notice } from '@/composables/notices';
import { groupPath } from '@/composables/slug';
import {
  keepEverySuggestion,
  useMenuEmptyState,
  useStartingList,
} from '@/composables/suggestionField';
import { roleLabel, ROLES, ROLE_ICONS, siteRoleHint } from '@/format';
import { t } from '@/i18n';

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

/** Whether this site role would change anything, or the group role stays wider. */
function widens(row: Row, role: Role): boolean {
  return row.groupRole === null || ROLES.indexOf(role) > ROLES.indexOf(row.groupRole);
}

/** What someone keeps when their site role goes or gets narrower. */
function keepsViaGroup(role: Role): string {
  return t('admin.siteMembers.keepsViaGroup', { role: roleLabel(role).toLowerCase() });
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
/** Lezer by default, as on the group: promoting is the deliberate step. */
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
    return t('admin.siteMembers.form.tooShort');
  }
  return searching.value
    ? t('admin.siteMembers.form.searching')
    : t('admin.siteMembers.form.noSuggestions');
});

const identifierField = ref<HTMLElement | null>(null);

/**
 * Someone with a site role of their own is marked, not held back: what that
 * role is and where it is changed is what the refusal says, which beats a row
 * that cannot be clicked and does not say why. A groepslid is offered like
 * anyone else, with the role it already reaches this site with, since a site
 * role only widens.
 */
function suggestionText(person: MemberSuggestion): string {
  const label = person.name || person.email;
  if (person.alreadyMember) {
    return t('admin.siteMembers.suggestion.alreadyMember', { name: label });
  }
  if (person.groupRole !== null) {
    return t('admin.siteMembers.suggestion.viaGroup', {
      name: label,
      role: roleLabel(person.groupRole).toLowerCase(),
    });
  }
  return label;
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
  newLabel.value = nameOf(identifier) || identifier;
  emptyField.value = false;
}

/**
 * The name behind a picked address, looked up in both lists rather than in the
 * one on screen: filling the field is itself what swaps the starting list for
 * the search answers, so by now the list it came out of may be the other one.
 */
function nameOf(identifier: string): string {
  const person =
    suggestions.value.find((candidate) => candidate.identifier === identifier) ??
    groupSuggestions.value.find((candidate) => candidate.identifier === identifier);
  return person?.name ?? '';
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

/**
 * The group as the starting point, the whole platform once someone types. On
 * what is on screen, not on what is picked: a pick leaves the field holding a
 * name, and the typing that led to it is what the answers belong to.
 */
const shownSuggestions = computed<MemberSuggestion[]>(() =>
  newLabel.value.trim() === '' ? groupSuggestions.value : suggestions.value,
);

/**
 * Whether the list on screen is still the group it starts with. The open menu
 * covers the help text under the field, so while it hides that line the list
 * has to say for itself that there is more behind it than the group.
 */
const startingList = computed(() => newLabel.value.trim().length < SEARCH_MIN_LENGTH);

// An empty menu opening on arrival would only read as a broken dropdown.
useStartingList(identifierField, () => shownSuggestions.value.length > 0);

// The rows arrive after the menu has already decided that it is empty.
useMenuEmptyState(identifierField, () => shownSuggestions.value);

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

  newIdentifier.value = '';
  newLabel.value = '';
  clearSuggestions();
  // Someone already on the list gets no provisional row: they are either on
  // this list twice or they move up out of the inherited block, and both need
  // the server's answer. A second row would get the same :key.
  const newRow = !rows.value.some((row) => row.identifier === identifier);
  if (newRow) {
    provisional.value = [
      ...provisional.value,
      {
        memberId: '',
        identifier,
        name: identifier,
        email: identifier,
        groupRole: null,
        siteRole: newRole.value,
        effectiveRole: newRole.value,
      },
    ];
  }
  try {
    const member = await props.add(identifier, newRole.value);
    emit('added', member);
  } catch (error) {
    notify(t('admin.siteMembers.addFailed', { name: identifier }), error, identifier);
  } finally {
    if (newRow) {
      provisional.value = provisional.value.filter((row) => row.identifier !== identifier);
    }
  }
}

/** The row whose site role is up for removal, once the menu asked for it. */
const removing = ref<Row | null>(null);
const removeBusy = ref(false);

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
      details: widens(row, role) ? undefined : keepsViaGroup(row.groupRole!),
      testid: `siterol-${row.identifier}-${role}`,
      run: () => void onRole(row, role),
    })),
    {
      text: t('admin.siteMembers.action.remove'),
      icon: 'trash',
      destructive: true,
      testid: `siterol-weghalen-${row.identifier}`,
      run: () => {
        removing.value = row;
      },
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

async function confirmRemove(): Promise<void> {
  const row = removing.value;
  /* v8 ignore next -- the modal only confirms while it is open, so there is a row. */
  if (row === null) return;
  removeBusy.value = true;
  try {
    await onRemove(row);
  } finally {
    removeBusy.value = false;
    removing.value = null;
  }
}

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
    <!-- A native details/summary rather than a toggle of our own: it is
         keyboard operable by itself, announces its open state, and lets the
         browser find text inside it with ctrl+F while it is closed. NLDD has
         no accordion component, so the styling hangs on its tokens. -->
    <details v-if="inherited.length > 0" class="via-groep" data-testid="leden-via-groep">
      <summary data-testid="leden-via-groep-samenvatting">{{ inheritedSummary }}</summary>

      <nldd-container layout="stack" gap="8" class="via-groep-inhoud">
        <nldd-text size="sm" color="secondary">
          {{ inheritedNote[0]
          }}<nldd-link
            :href="groupPath(group, '/-/members')"
            size="inherit"
            data-testid="leden-naar-groep"
          >{{ t('admin.siteMembers.inherited.link', { group: groupName }) }}</nldd-link
          >{{ inheritedNote[1] }}
        </nldd-text>

        <nldd-table
          class="member-table"
          :accessible-label="t('admin.siteMembers.inherited.table')"
          data-testid="leden-via-groep-lijst"
          :columns="COLUMNS_INHERITED"
          :sm-columns="COLUMNS_INHERITED_SM"
        >
          <nldd-table-row slot="header">
            <nldd-text-cell
              size="sm"
              color="secondary"
              :text="t('admin.column.member')"
            ></nldd-text-cell>
            <nldd-text-cell
              size="sm"
              color="secondary"
              :text="t('admin.column.role')"
            ></nldd-text-cell>
          </nldd-table-row>
          <nldd-table-row v-for="row in inherited" :key="row.identifier">
            <nldd-text-cell :text="row.name" :supporting-text="row.email"></nldd-text-cell>
            <nldd-text-cell
              size="sm"
              color="secondary"
              :text="roleLabel(row.effectiveRole)"
            ></nldd-text-cell>
          </nldd-table-row>
        </nldd-table>
      </nldd-container>
    </details>

    <nldd-container layout="stack" gap="16">
      <nldd-table
        class="member-table"
        :accessible-label="t('admin.siteMembers.table')"
        data-testid="leden-lijst"
        :columns="COLUMNS"
        :sm-columns="COLUMNS_SM"
      >
        <nldd-table-row slot="header">
          <nldd-text-cell
            size="sm"
            color="secondary"
            :text="t('admin.column.member')"
          ></nldd-text-cell>
          <nldd-text-cell
            size="sm"
            color="secondary"
            :text="t('admin.column.role')"
            hide-below="md"
          ></nldd-text-cell>
          <!-- Named but not shown, as on the group's Leden tab: a word above
               one icon button says nothing, and a columnheader without a name
               is an axe violation. -->
          <nldd-text-cell size="sm" color="secondary" horizontal-alignment="right">
            <span class="alleen-schermlezer">{{ t('admin.column.actions') }}</span>
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
            size="sm"
            color="secondary"
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

      <!-- A box, not just spacing: nldd-box draws its own surface, which is
         what says at a glance that these controls belong together and are
         not one more row of the table above. -->
    <nldd-box>
      <nldd-container layout="stack" padding="16">
        <nldd-form data-testid="siterol-formulier" @submit.prevent="onAdd">
          <nldd-form-section
            :text="t('admin.siteMembers.form.heading')"
            :supporting-text="t('admin.siteMembers.form.hint')"
          >
            <!-- Who comes first, what they may second: the role only makes
                 sense once you know whom it is for. -->
            <!-- A combo box without allow-custom: typing searches, but only a
                 name from the list can be submitted. An address that is not in
                 it belongs to nobody who has ever logged in on the beheer, and
                 the server would refuse it. -->
            <nldd-form-field :label="t('admin.siteMembers.form.identifier')">
              <nldd-combo-box
                ref="identifierField"
                name="identifier"
                :placeholder="t('admin.siteMembers.form.identifier.placeholder')"
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
                  data-testid="siterol-suggesties"
                >
                  <!-- The footer sits outside role="menu", so it is neither a
                       choosable option nor a stop for arrow keys; it holds no
                       control, so it is no tab stop either. The slot is
                       unpadded, hence the container. -->
                  <nldd-container v-if="startingList" slot="footer" padding="8">
                    <nldd-text size="sm" color="secondary" data-testid="siterol-verder-zoeken">
                      {{ t('admin.siteMembers.form.searchFurther') }}
                    </nldd-text>
                  </nldd-container>
                  <nldd-menu-item
                    v-for="person in shownSuggestions"
                    :key="person.identifier"
                    :text="suggestionText(person)"
                    :value="person.identifier"
                    :details="suggestionDetails(person)"
                    :data-testid="`siterol-suggestie-${person.identifier}`"
                  ></nldd-menu-item>
                </nldd-menu>
              </nldd-combo-box>
              <nldd-form-field-help-text>
                {{ t('admin.siteMembers.form.identifier.help') }}
              </nldd-form-field-help-text>
              <!-- The value to check, handed over rather than read off the
                   control: the list re-checks on the control's `input` event,
                   and picking from the menu is a `change` without one. -->
              <nldd-validation-list :value="newIdentifier">
                <nldd-validation-item id="siterol-toevoegen-vereist" required>
                  {{ t('admin.siteMembers.form.identifier.required') }}
                </nldd-validation-item>
              </nldd-validation-list>
            </nldd-form-field>

            <nldd-form-field :label="t('admin.siteMembers.form.role')">
              <nldd-dropdown>
                <select v-model="newRole" name="rol" data-testid="siterol-nieuw">
                  <option v-for="role in ROLES" :key="role" :value="role">
                    {{ roleLabel(role) }}
                  </option>
                </select>
              </nldd-dropdown>
              <nldd-form-field-help-text>{{ siteRoleHint(newRole) }}</nldd-form-field-help-text>
            </nldd-form-field>

            <nldd-form-actions>
              <nldd-button
                variant="primary"
                :text="t('admin.siteMembers.form.submit')"
                type="submit"
              ></nldd-button>
            </nldd-form-actions>
          </nldd-form-section>
          </nldd-form>
      </nldd-container>
    </nldd-box>
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
    @close="removing = null"
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

.via-groep {
  max-width: var(--plak-table-max-width);
  border: var(--semantics-surfaces-border-width, 1px) solid
    var(--semantics-surfaces-base-border-color, #e6e8ea);
  border-radius: var(--semantics-surfaces-corner-radius, 12px);
  background: var(--semantics-surfaces-tinted-background-color, #f6f7f8);
}

/* No display: flex or block here: both drop the native disclosure triangle in
   Chrome and Safari, and there is no component icon to put in its place. */
.via-groep > summary {
  padding: 12px 16px;
  cursor: pointer;
  color: var(--semantics-content-color, inherit);
  font: var(--primitives-font-body-md-semi-bold-snug, inherit);
}

.via-groep > summary:focus-visible {
  outline: var(--semantics-focus-ring-outline);
  outline-offset: var(--semantics-focus-ring-outline-offset);
  box-shadow: var(--semantics-focus-ring-box-shadow);
  border-radius: var(--semantics-surfaces-corner-radius, 12px);
}

.via-groep-inhoud {
  padding: 0 16px 16px;
}
</style>
