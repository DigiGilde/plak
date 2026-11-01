/**
 * Who runs this installation, for the privacy and accessibility statements.
 * Plak is installed by more than one organisation, so the statements name
 * the operator from `GET /operator` and otherwise "the organisation that runs
 * this installation".
 */
import { ref, type Ref } from 'vue';

import { operator } from '@/api/toon';
import type { Operator } from '@/api/types';

/**
 * Null until the answer is in, and stays null when it does not come: a
 * statement without a name is still right, a broken page is not.
 */
export function useOperator(): Ref<Operator | null> {
  const result = ref<Operator | null>(null);
  operator().then(
    (answer) => {
      result.value = answer;
    },
    () => undefined,
  );
  return result;
}
