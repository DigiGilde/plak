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

import RowActions, { type RowAction } from '@/components/RowActions.vue';
import type { MemberSuggestion, Role, SiteMember } from '@/api/types';
import { looksLikeEmail, useMemberSearch } from '@/composables/memberSearch';
import { useNotices, type Notice } from '@/composables/notices';
import { roleLabel, ROLES, ROLE_ICONS, siteRoleHint } from '@/format';
import { t } from '@/i18n';

const props = defineProps<{
  members: SiteMember[];
  /** Slug of the group, for the link to its own Leden tab. */
  group: string;
  /** Display name of the group, as the summary of the collapsed block names it. */
  groupName: string;
  add: (identifier: string, role: Role) => Promise<SiteMember>;
  remove: (identifier: string) => Promise<void>;
  setRole: (identifier: string, role: Role) => Promise<SiteMember>;
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

/** Someone who already has a role here stays in the list, marked and inert. */
function suggestionText(person: MemberSuggestion): string {
  const label = person.name || person.email;
  return person.alreadyMember
    ? t('admin.siteMembers.suggestion.alreadyMember', { name: label })
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
  // Someone already on the list gets no provisional row: they are either on
  // this list twice or they move up out of the inherited block, and both need
  // the server's answer. A second row would get the same :key.
  const newRow = !rows.value.some((row) => row.identifier === identifier);
  if (newRow) {
    provisional.value = [
      ...provisional.value,
      {
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
      details: row.groupRole
        ? keepsViaGroup(row.groupRole)
        : t('admin.siteMembers.action.remove.noAccess'),
      testid: `siterol-weghalen-${row.identifier}`,
      run: () => void onRemove(row),
    },
  ];
}

async function onRole(row: Row, role: Role): Promise<void> {
  try {
    emit('roleChanged', await props.setRole(row.identifier, role));
  } catch (error) {
    notify(t('admin.siteMembers.roleFailed', { name: row.name }), error);
  }
}

async function onRemove(row: Row): Promise<void> {
  losingSiteRole.value = [...losingSiteRole.value, row.identifier];
  try {
    await props.remove(row.identifier);
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
            :href="`/${group}/-/members`"
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
            <!-- A combo box with allow-custom, not a picker: typing a name is
                 the short road, but the list only holds people who have logged
                 in on the beheer, and an address that is not in it still has to
                 be submittable. -->
            <nldd-form-field :label="t('admin.siteMembers.form.identifier')">
              <nldd-combo-box
                name="identifier"
                allow-custom
                :placeholder="t('admin.siteMembers.form.identifier.placeholder')"
                required
                :value="newIdentifier"
                :text="newLabel"
                :invalid="emptyField || notAnEmail || undefined"
                :unmet="notAnEmail ? 'siterol-toevoegen-geen-email' : undefined"
                @input="onIdentifierInput"
                @change="onIdentifierChange"
              >
                <nldd-menu
                  :empty-text="t('admin.siteMembers.form.noSuggestions')"
                  data-testid="siterol-suggesties"
                >
                  <nldd-menu-item
                    v-for="person in suggestions"
                    :key="person.identifier"
                    :text="suggestionText(person)"
                    :value="person.identifier"
                    :details="suggestionDetails(person)"
                    :disabled="person.alreadyMember || undefined"
                    :data-testid="`siterol-suggestie-${person.identifier}`"
                  ></nldd-menu-item>
                </nldd-menu>
              </nldd-combo-box>
              <nldd-form-field-help-text>
                {{ t('admin.siteMembers.form.identifier.help') }}
              </nldd-form-field-help-text>
              <nldd-validation-list>
                <nldd-validation-item id="siterol-toevoegen-vereist" required>
                  {{ t('admin.siteMembers.form.identifier.required') }}
                </nldd-validation-item>
                <!-- No rule of its own: named in the combo box's `unmet`
                     attribute instead, since it depends on whether the typed
                     text is a pick or a plain e-mail address, not on a
                     pattern the field can check by itself. -->
                <nldd-validation-item id="siterol-toevoegen-geen-email">
                  {{ t('admin.siteMembers.form.identifier.invalid') }}
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
