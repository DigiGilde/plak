<script setup lang="ts">
// Platform administration: deactivating members and reactivating them
// Access to this route is already enforced by the router guard
// (meta.platformAdminRequired); this page therefore assumes the visitor is
// a platform admin and focuses on the activate/deactivate flow. Activating and
// deactivating are optimistic: the status lands on the row straight away, the
// API confirms in the background, and on failure the page rolls back with a
// notification.
//
// A table rather than a list: four pieces of information per member only read
// as a comparison when they line up on the same x across every row.
//
// The words here are about access, not about activation. "Deactiveren" says
// what the database does; "Toegang intrekken" says what happens to the person,
// and that is the thing an administrator is deciding about.
//
// One column says where someone stands: their role while they have access,
// and otherwise that they have none. Those are not two facts to a reader:
// someone whose access is withdrawn has no role to speak of, so the column
// carries whichever of the two is true and colours it.
//
// The menu offers only what the API would actually accept. Three things it
// refuses (yourself, the last active beheerder, the bootstrap account), and
// offering them anyway would be a control that exists to fail.
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import { ApiError } from '../api/client';
import {
  platformMembers,
  activatePlatformMember,
  deactivatePlatformMember,
  setPlatformRole,
} from '../api/plak';
import type { Member, MemberStatus } from '../api/types';
import RowActions, { type RowAction } from '../components/RowActions.vue';
import VolumeUsage from '../components/VolumeUsage.vue';
import { setBreadcrumbs } from '../composables/breadcrumbs';
import { currentMemberState, fetchCurrentMember } from '../composables/currentMember';
import { useNotices } from '../composables/notices';
import { formatDate, formatTimestamp } from '../format';
import { t } from '../i18n';

type SortKey = 'name' | 'role' | 'created' | 'activity';
type Direction = 'ascending' | 'descending';

interface SortableColumn {
  key: SortKey;
  label: string;
  /** Which way round the first click sorts; a date starts at the newest. */
  initial: Direction;
}

// A computed rather than a constant: the labels follow a change of language
// without a reload.
const SORTABLE = computed<SortableColumn[]>(() => [
  { key: 'name', label: t('admin.column.member'), initial: 'ascending' },
  { key: 'role', label: t('admin.column.role'), initial: 'descending' },
  { key: 'created', label: t('admin.members.column.created'), initial: 'descending' },
  { key: 'activity', label: t('admin.members.column.activity'), initial: 'descending' },
]);

/**
 * The columns, set once on the table so every row lines up on the same x.
 * Only the member column stretches; the rest are fixed at the width their
 * widest content actually measures in Chromium at a root font of 16 px, with
 * the 18 px cell text: role 170 px ("Platform administrator"), created 99 px
 * ("27 Sept 2026"), last activity 151 px ("30 Sept 2026, 20:56") and the menu
 * button 32 px, each with at least 16 px of room on top. The sortable headers
 * are narrower than their contents.
 *
 * Fixed rather than `auto`, because an auto column shrinks to min-content
 * under pressure and breaks a date across two lines.
 */
const COLUMNS = 'minmax(12rem, 1fr) 12rem 7.5rem 11rem 3rem';
/** Below md only the member and the menu remain; the rest drops out. */
const COLUMNS_SM = 'minmax(0, 1fr) 3rem';

const route = useRoute();
const members = ref<Member[]>([]);
const loading = ref(true);
const errorMessage = ref<string | null>(null);
const sortKey = ref<SortKey>('name');
const direction = ref<Direction>('ascending');
const query = ref('');

const { notices, notify, dismissNotice } = useNotices();
const { member: me } = currentMemberState();

/** Name and email both, because an admin looking someone up has one or the other. */
const found = computed<Member[]>(() => {
  const needle = query.value.trim().toLocaleLowerCase('nl');
  if (needle === '') return members.value;
  return members.value.filter((member) =>
    `${member.name} ${member.email}`.toLocaleLowerCase('nl').includes(needle),
  );
});

const sortedMembers = computed<Member[]>(() => {
  const rows = [...found.value];
  const sign = direction.value === 'ascending' ? 1 : -1;
  if (sortKey.value === 'name') {
    // localeCompare with the Dutch collation, so "Zoë" sorts under the Z
    // instead of after every unaccented name.
    return rows.sort(
      (a, b) => sign * (a.name || a.email).localeCompare(b.name || b.email, 'nl'),
    );
  }
  if (sortKey.value === 'role') {
    // Beheerders first when descending: on this page they are what you look
    // for. Within one role the list stays alphabetical, so it never looks
    // shuffled.
    const rank = (member: Member): number => (member.platformRole === 'admin' ? 1 : 0);
    return rows.sort(
      (a, b) =>
        sign * (rank(a) - rank(b)) ||
        (a.name || a.email).localeCompare(b.name || b.email, 'nl'),
    );
  }
  const field = sortKey.value === 'created' ? 'createdAt' : 'lastLoginAt';
  return rows.sort((a, b) => {
    // A member who never logged in has no activity date. Those go last whichever
    // way the column is sorted, rather than sorting as 1970 and heading the
    // "oldest first" end of the list.
    const left = a[field] ?? '';
    const right = b[field] ?? '';
    if (left === right) return 0;
    if (left === '') return 1;
    if (right === '') return -1;
    return sign * left.localeCompare(right);
  });
});

/** ascending / descending for the sorted column, `none` for the others (WAI-ARIA). */
function ariaSort(key: SortKey): Direction | 'none' {
  return sortKey.value === key ? direction.value : 'none';
}

function sortOn(column: SortableColumn): void {
  if (sortKey.value === column.key) {
    direction.value = direction.value === 'ascending' ? 'descending' : 'ascending';
    return;
  }
  sortKey.value = column.key;
  direction.value = column.initial;
}

async function refresh(): Promise<void> {
  loading.value = true;
  errorMessage.value = null;
  try {
    // Who is logged in decides which actions this page offers, so it asks
    // rather than assuming the app shell already did. The composable caches,
    // so this costs no second request.
    const [rows] = await Promise.all([platformMembers(), fetchCurrentMember().catch(() => null)]);
    members.value = rows;
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError ? error.problem.title : t('admin.members.loadFailed');
  } finally {
    loading.value = false;
  }
}

/**
 * Where this member stands. Without access the platform role says nothing, so
 * the column says the thing that is actually true; the stored role comes back
 * when access does.
 *
 * The colour repeats what the words already say rather than carrying it: per
 * WCAG 1.4.1 colour may not be the only signal, and "Geen toegang" reads as
 * itself in a screen reader and in forced colours.
 */
function standing(member: Member): { text: string; color: 'content' | 'warning' | 'critical' } {
  if (member.status === 'deactivated') {
    return { text: t('admin.members.standing.noAccess'), color: 'critical' };
  }
  if (member.platformRole === 'admin') {
    return { text: t('admin.members.standing.admin'), color: 'content' };
  }
  return { text: t('admin.members.standing.member'), color: 'content' };
}

/** You, in the list. Your own row is the one the API refuses every change on. */
function isMe(member: Member): boolean {
  return me.value?.id === member.id;
}

/** True when removing this member's beheerder role would leave the platform without one. */
function isLastAdmin(member: Member): boolean {
  if (member.platformRole !== 'admin' || member.status !== 'active') return false;
  return (
    members.value.filter((row) => row.platformRole === 'admin' && row.status === 'active').length === 1
  );
}

/**
 * The access action, named after what happens to the person rather than after
 * the column in the database. Two states, two verbs: someone who has access
 * can lose it, someone who lost it can get it back.
 */
function accessAction(member: Member): RowAction {
  const active = member.status === 'active';
  return {
    text: active ? t('admin.members.action.revoke') : t('admin.members.action.restore'),
    icon: active ? 'lock' : 'lock-open',
    testid: `member-access-${member.id}`,
    run: () => void toggle(member),
  };
}

/**
 * Only what this member can actually have done to them.
 *
 * Your own row is left bare: the API refuses both changes there, and you do
 * not need telling that you are yourself. The other two refusals are not
 * self-evident, so those items stay and say why, greyed out.
 */
function actionsFor(member: Member): RowAction[] {
  if (isMe(member)) return [];

  const admin = member.platformRole === 'admin';
  const blocked = member.isBootstrap
    ? t('admin.members.blocked.bootstrap')
    : isLastAdmin(member)
      ? t('admin.members.blocked.lastAdmin')
      : undefined;

  const access = accessAction(member);
  const withdrawing = member.status === 'active';
  return [
    {
      ...access,
      disabled: Boolean(blocked) && withdrawing,
      details: blocked && withdrawing ? blocked : access.details,
    },
    {
      text: admin ? t('admin.members.action.demote') : t('admin.members.action.promote'),
      icon: admin ? 'person' : 'person-2',
      disabled: Boolean(blocked) && admin,
      details: blocked && admin ? blocked : undefined,
      testid: `member-role-${member.id}`,
      run: () => void changeRole(member, admin ? 'member' : 'admin'),
    },
  ];
}

async function changeRole(member: Member, role: 'admin' | 'member'): Promise<void> {
  const previous = member.platformRole;
  member.platformRole = role;
  try {
    replaceMember(await setPlatformRole(member.id, role));
  } catch (error) {
    member.platformRole = previous;
    // The backend refuses three cases on purpose (yourself, the last active
    // beheerder, the bootstrap account); its message says which one.
    notify(t('admin.members.roleFailed', { name: member.name }), error);
  }
}

async function toggle(member: Member): Promise<void> {
  if (member.status === 'active') {
    await setStatus(
      member,
      'deactivated',
      deactivatePlatformMember,
      t('admin.members.deactivateFailed', { name: member.name }),
    );
    return;
  }
  await setStatus(
    member,
    'active',
    activatePlatformMember,
    t('admin.members.activateFailed', { name: member.name }),
  );
}

async function setStatus(
  member: Member,
  target: MemberStatus,
  action: (memberId: string) => Promise<Member>,
  errorText: string,
): Promise<void> {
  const previous = member.status;
  member.status = target;
  try {
    replaceMember(await action(member.id));
  } catch (error) {
    member.status = previous;
    notify(errorText, error);
  }
}

function replaceMember(updated: Member): void {
  const index = members.value.findIndex((l) => l.id === updated.id);
  /* v8 ignore start -- the API always answers with the same id it was called
     for; the guard is only for a response that somehow does not, which no
     test can provoke through the real request/response contract. */
  if (index !== -1) {
    members.value.splice(index, 1, updated);
  }
  /* v8 ignore stop */
}

function onSearch(event: Event): void {
  const detail = (event as CustomEvent<{ value?: string }>).detail;
  const targetValue = (event.target as HTMLInputElement | null)?.value;
  // Guards an event with neither a detail nor a target value at all (not
  // something nldd-search-field, or a native input, ever dispatches);
  // unreachable without fabricating a malformed event.
  /* v8 ignore start */
  query.value = detail?.value ?? targetValue ?? '';
  /* v8 ignore stop */
}

onMounted(() => {
  setBreadcrumbs(route.path, [
    { text: t('nav.overview'), href: '/' },
    { text: t('nav.platform') },
  ]);
  void refresh();
});
</script>

<template>
  <nldd-simple-section>
    <nldd-container layout="wrap" gap="16" vertical-alignment="center" class="page-header">
      <nldd-title :size="1"><h1>{{ t('nav.platform') }}</h1></nldd-title>
      <nldd-search-field
        v-if="!loading && !errorMessage"
        :accessible-label="t('admin.members.search.label')"
        :placeholder="t('admin.members.search.placeholder')"
        name="search"
        data-testid="members-search"
        @input="onSearch"
      ></nldd-search-field>
    </nldd-container>

    <nldd-spacer size="24"></nldd-spacer>

    <nldd-activity-indicator
      v-if="loading"
      show-text
      :text="t('admin.members.loading')"
    ></nldd-activity-indicator>

    <nldd-banner
      v-else-if="errorMessage"
      variant="critical"
      :text="errorMessage"
      :heading-level="2"
    >
      <nldd-button
        slot="actions"
        variant="critical-tinted"
        :text="t('admin.retry')"
        @click="refresh"
      ></nldd-button>
    </nldd-banner>

    <nldd-table
      v-else
      background="tinted"
      :accessible-label="t('admin.members.table.label')"
      :columns="COLUMNS"
      :sm-columns="COLUMNS_SM"
    >
      <nldd-table-row slot="header">
        <!-- The design system has no sortable header, so the control is a plain
             button inside the header cell. aria-sort belongs on the element
             carrying role="columnheader", and nldd-table puts that role on the
             nldd-text-cell itself, so it goes here rather than on the button. -->
        <nldd-text-cell
          v-for="column in SORTABLE"
          :key="column.key"
          :aria-sort="ariaSort(column.key)"
          :hide-below="column.key === 'name' ? undefined : 'md'"
        >
          <button type="button" class="sort-header" @click="sortOn(column)">
            <strong>{{ column.label }}</strong>
            <!-- The arrow is decoration: aria-sort already announces the order,
                 and a screen reader would otherwise read it twice. -->
            <span aria-hidden="true" class="sort-header__arrow">{{
              sortKey === column.key ? (direction === 'ascending' ? '↑' : '↓') : ''
            }}</span>
          </button>
        </nldd-text-cell>
        <!-- Named but not shown: a word above one icon button says nothing a
             reader needs, and at this column width it broke across two lines.
             It cannot be left out altogether, because a columnheader without a
             name is an axe violation (empty-table-header) and leaves a screen
             reader announcing the menu cell with no column to hang it on. -->
        <nldd-text-cell horizontal-alignment="right">
          <span class="visually-hidden">{{ t('admin.column.actions') }}</span>
        </nldd-text-cell>
      </nldd-table-row>

      <nldd-inline-dialog
        slot="empty"
        :icon="query === '' ? 'person-2' : 'search'"
        :text="query === '' ? t('admin.members.empty') : t('admin.members.notFound')"
        :supporting-text="
          query === '' ? undefined : t('admin.members.notFound.detail', { query })
        "
      ></nldd-inline-dialog>

      <nldd-table-row v-for="member in sortedMembers" :key="member.id">
        <nldd-text-cell :text="member.name" :supporting-text="member.email"></nldd-text-cell>
        <nldd-text-cell
          :color="standing(member).color"
          :text="standing(member).text"
          hide-below="md"
        ></nldd-text-cell>
        <nldd-text-cell
          :text="formatDate(member.createdAt)"
          hide-below="md"
        ></nldd-text-cell>
        <nldd-text-cell
          :text="formatTimestamp(member.lastLoginAt)"
          hide-below="md"
        ></nldd-text-cell>
        <!-- Right aligned, so the menu of every row sits on one edge with the
             table. -->
        <nldd-cell horizontal-alignment="right">
          <RowActions :label="member.name" :actions="actionsFor(member)" />
        </nldd-cell>
      </nldd-table-row>
    </nldd-table>

    <nldd-spacer size="32"></nldd-spacer>
    <VolumeUsage />

    <nldd-notification
      v-for="notice in notices"
      :key="notice.id"
      variant="critical"
      :text="notice.text"
      :supporting-text="notice.detail"
      @dismiss="dismissNotice(notice.id)"
    ></nldd-notification>
  </nldd-simple-section>
</template>

<style scoped>
/* A header cell that sorts. Stripped back to the text it replaces: the cell
   around it already sets the type scale and colour, so the button inherits
   both and only adds the pointer, the focus ring and room for the arrow. */
.sort-header {
  display: inline-flex;
  align-items: center;
  gap: var(--primitives-space-4);
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: inherit;
  cursor: pointer;
  /* 24px, so the target keeps the minimum size of WCAG 2.5.8. */
  min-height: var(--primitives-space-24);
}

.sort-header:focus-visible {
  outline: var(--semantics-focus-ring-outline);
  outline-offset: var(--semantics-focus-ring-outline-offset);
  box-shadow: var(--semantics-focus-ring-box-shadow);
}

/* Reserved whether or not this column is the sorted one, so the header does not
   jump sideways when you sort on another column. */
.sort-header__arrow {
  display: inline-block;
  min-width: var(--primitives-space-8);
}
</style>
