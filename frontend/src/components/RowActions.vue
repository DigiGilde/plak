<script setup lang="ts">
/**
 * The actions on one row of a table, in one shape for the whole admin
 * interface: a single control, always in the same place.
 *
 * Without it, every list solves this its own way: a button here, a differently
 * coloured button there, a list item with two buttons somewhere else, so the
 * row reads differently per page while the job is the same. One menu behind
 * one button fixes that and keeps the row quiet, which matters because these
 * are lists you read far more often than you act on.
 *
 * What someone IS stays on the row itself (a column, or the overline of the
 * first cell). This component only carries what you can DO.
 *
 * Put it in an <nldd-cell horizontal-alignment="right">, so the control of
 * every row sits on one edge with the table.
 */
import { t } from '@/i18n';

export interface RowAction {
  /** The label, naming what pressing it does: "Toegang intrekken", not "Actief". */
  text: string;
  /** Marks a destructive action; the menu draws it in the critical colour. */
  destructive?: boolean;
  disabled?: boolean;
  icon?: string;
  /** Short second label on the right of the item, for a consequence in one or two words. */
  details?: string;
  /** Distinguishes this action in tests and in the DOM. */
  testid?: string;
  run: () => void;
}

/**
 * One setting of the row, offered as a radio group above the actions.
 *
 * A select beside the menu would be the obvious alternative, but two controls
 * on one row do not survive 200 percent text on a 320 px screen: the row never
 * wraps, so both of them run off the edge (WCAG 1.4.10). The menu has the room
 * a row does not.
 */
export interface RowChoice {
  /** Title above the options, naming what is being chosen: "Toegang". */
  label: string;
  /** The option that is currently set. */
  value: string;
  options: { value: string; text: string; details?: string; testid?: string }[];
  pick: (value: string) => void;
}

const props = defineProps<{
  actions: RowAction[];
  /** Names the menu for a screen reader: the row it belongs to. */
  label: string;
  choice?: RowChoice;
}>();
</script>

<template>
  <!-- An icon button rather than labelled buttons: a row carries one to three
       actions depending on its state, and a word for each of them would
       compete with the content you came to read. popup-type sets aria-haspopup
       and aria-expanded itself. -->
  <nldd-icon-button
    v-if="props.actions.length > 0 || props.choice"
    size="sm"
    variant="neutral-transparent"
    icon="ellipsis"
    popup-type="menu"
    class="row-actions"
    :accessible-label="t('admin.rowActions.label', { label: props.label })"
    :data-testid="`actions-${props.label}`"
  >
    <nldd-menu slot="popup" placement="bottom-end">
      <!-- The group first: it carries its own divider towards what follows,
           and a destructive action belongs at the bottom, away from the
           pointer's resting place. -->
      <nldd-menu-group v-if="props.choice" :text="props.choice.label">
        <nldd-menu-item
          v-for="option in props.choice.options"
          :key="option.value"
          type="radio"
          :text="option.text"
          :details="option.details"
          :selected="option.value === props.choice.value || undefined"
          :data-testid="option.testid"
          @select="props.choice.pick(option.value)"
        ></nldd-menu-item>
      </nldd-menu-group>
      <nldd-menu-divider v-if="props.choice && props.actions.length > 0"></nldd-menu-divider>
      <nldd-menu-item
        v-for="action in props.actions"
        :key="action.text"
        :text="action.text"
        :icon="action.icon"
        :details="action.details"
        :destructive="action.destructive || undefined"
        :disabled="action.disabled || undefined"
        :data-testid="action.testid"
        @select="action.run()"
      ></nldd-menu-item>
    </nldd-menu>
  </nldd-icon-button>
</template>

<style scoped>
/* The icon set has only a horizontal ellipsis, and horizontal reads as "this
   row continues" while vertical reads as "this row has a menu". Rotating the
   button rather than the icon, because the icon lives in the shadow root; the
   button is square, so its focus ring and hover come out identical either
   way. */
.row-actions {
  transform: rotate(90deg);
}
</style>
