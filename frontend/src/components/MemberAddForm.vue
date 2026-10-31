<script setup lang="ts">
/**
 * The form under the member table of a group and of a site: who first, what
 * they may second, then add. The state and the submit live in
 * `useMemberAddForm`; the wording and the test ids come from the page that
 * owns the list.
 */
import type { MemberSuggestion } from '@/api/types';
import { keepEverySuggestion } from '@/composables/suggestionField';
import type { useMemberAddForm } from '@/composables/memberAddForm';
import { ROLES, roleLabel } from '@/format';

const props = defineProps<{
  form: ReturnType<typeof useMemberAddForm<unknown>>;
  labels: {
    legend: string;
    supportingText: string;
    identifier: string;
    placeholder: string;
    identifierHelp: string;
    required: string;
    role: string;
    submit: string;
  };
  /** The explanation under the role picker, for the role that is picked. */
  roleHint: string;
  suggestionText: (person: MemberSuggestion) => string;
  /** `<prefix>-form`, `<prefix>-suggestions`, `<prefix>-suggestion-<address>`, `<prefix>-add-required`. */
  testidPrefix: string;
  roleTestid: string;
}>();

const {
  newIdentifier,
  newLabel,
  newRole,
  emptyField,
  identifierField,
  shownSuggestions,
  emptyText,
  onIdentifierInput,
  onIdentifierChange,
  onAdd,
} = props.form;
</script>

<template>
  <!-- A box, not just spacing: nldd-box draws its own surface, which is what
       says at a glance that these controls belong together and are not one
       more row of the table above. -->
  <nldd-box>
    <nldd-container layout="stack" padding="16">
      <nldd-form :data-testid="`${testidPrefix}-form`" @submit.prevent="onAdd">
        <!-- A real fieldset with a legend, which is what nldd-form-section
             renders in the light DOM. Without it the two fields and the button
             float under the table as three unrelated things, and a screen
             reader announces them without ever saying what they are for. -->
        <nldd-form-section :text="labels.legend" :supporting-text="labels.supportingText">
          <!-- Who comes first, what they may second: the role only makes sense
               once you know whom it is for. -->
          <!-- A combo box without allow-custom: typing searches, but only a
               name from the list can be submitted. An address that is not in
               it belongs to nobody who has ever logged in on the admin, and
               the server would refuse it. -->
          <nldd-form-field :label="labels.identifier">
            <nldd-combo-box
              :ref="(element) => (identifierField = element as HTMLElement | null)"
              name="identifier"
              :placeholder="labels.placeholder"
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
                :data-testid="`${testidPrefix}-suggestions`"
              >
                <slot name="menu-footer" />
                <nldd-menu-item
                  v-for="person in shownSuggestions"
                  :key="person.identifier"
                  :text="suggestionText(person)"
                  :value="person.identifier"
                  :data-testid="`${testidPrefix}-suggestion-${person.identifier}`"
                ></nldd-menu-item>
              </nldd-menu>
            </nldd-combo-box>
            <nldd-form-field-help-text>
              {{ labels.identifierHelp }}
            </nldd-form-field-help-text>
            <!-- The value to check, handed over rather than read off the
                 control: the list re-checks on the control's `input` event,
                 and picking from the menu is a `change` without one. -->
            <nldd-validation-list :value="newIdentifier">
              <nldd-validation-item :id="`${testidPrefix}-add-required`" required>
                {{ labels.required }}
              </nldd-validation-item>
            </nldd-validation-list>
          </nldd-form-field>

          <!-- The role is picked while adding, not afterwards: it decides what
               someone may from their first minute, and lezer as the default
               makes promoting the deliberate step. -->
          <nldd-form-field :label="labels.role">
            <nldd-dropdown>
              <select v-model="newRole" name="role" :data-testid="roleTestid">
                <option v-for="role in ROLES" :key="role" :value="role">
                  {{ roleLabel(role) }}
                </option>
              </select>
            </nldd-dropdown>
            <nldd-form-field-help-text>
              {{ roleHint }}
              <slot name="role-help" />
            </nldd-form-field-help-text>
          </nldd-form-field>

          <nldd-form-actions>
            <nldd-button variant="primary" :text="labels.submit" type="submit"></nldd-button>
          </nldd-form-actions>
        </nldd-form-section>
      </nldd-form>
    </nldd-container>
  </nldd-box>
</template>
