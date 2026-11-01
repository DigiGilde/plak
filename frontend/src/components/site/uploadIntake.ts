/**
 * What every place that takes a site does with what it is given: read it,
 * check it against the limits, pack it when it is a folder, and say how that
 * went. The upload zone of an existing site and the sheet for a new one share
 * this, so the very first publish takes what every later one takes.
 */
import { ref } from 'vue';

import {
  canPack,
  checkBundle,
  formatCount,
  formatSize,
  pack,
  readChosenFolder,
  type DropResult,
} from './packing';
import { t } from '@/i18n';

export type IntakeStep = '' | 'reading' | 'packing';

type Outcome = { file: File; named: boolean; description: string } | { refusal: string };

function fileReady(file: File): string {
  return t('publish.upload.ready.file', { name: file.name, size: formatSize(file.size) });
}

async function outcomeOf(
  read: () => DropResult | Promise<DropResult>,
  onPacking: (done: number, total: number) => void,
): Promise<Outcome> {
  const dropped = await read();
  if (dropped.kind === 'refusal') return { refusal: dropped.reason };
  if (dropped.kind === 'file') {
    return { file: dropped.file, named: true, description: fileReady(dropped.file) };
  }

  const objection = checkBundle(dropped.name, dropped.files);
  if (objection) return { refusal: objection };
  if (!canPack()) return { refusal: t('publish.upload.error.cannotPack') };

  const packed = dropped.files.length;
  onPacking(0, packed);
  const archive = await pack(dropped.name, dropped.files, onPacking);
  const params = { count: formatCount(packed), size: formatSize(archive.size) };
  return {
    file: archive,
    named: dropped.name !== null,
    description:
      dropped.name === null
        ? t(packed === 1 ? 'publish.upload.ready.looseOne' : 'publish.upload.ready.loose', params)
        : t(packed === 1 ? 'publish.upload.ready.bundleOne' : 'publish.upload.ready.bundle', {
            ...params,
            name: dropped.name,
          }),
  };
}

/**
 * `adopt` receives the file that came out of a read, ready to be published.
 * `named` is false for loose contents: no folder lent its name to that archive.
 *
 * `discard` makes the caller forget its choice. What is handed over last is the
 * new state, so that happens as soon as a read starts: if the read is refused
 * there is nothing to publish, and the earlier choice does not come back.
 */
export function useUploadIntake(adopt: (file: File, named: boolean) => void, discard: () => void) {
  const step = ref<IntakeStep>('');
  const progress = ref<{ done: number; total: number } | null>(null);
  /** What is ready, in the user's own terms; empty until something is there. */
  const ready = ref('');
  const error = ref<string | null>(null);

  // Packing a folder takes a while. Whatever happens in the meantime (another
  // drop, a choice in the field, a sheet that closes) overrules what is under
  // way, and its result must not land afterwards.
  let latest = 0;

  /**
   * `read` runs before the first await, so a read that needs the drop event
   * can be handed in from the drop handler itself.
   */
  async function receive(read: () => DropResult | Promise<DropResult>): Promise<void> {
    // Before `latest` moves: emptying a field makes it report that through
    // noteChoice, and that must not overrule the read that is only now starting.
    discard();
    latest += 1;
    const mine = latest;
    error.value = null;
    ready.value = '';
    step.value = 'reading';
    progress.value = null;

    let outcome: Outcome;
    try {
      outcome = await outcomeOf(read, (done, total) => {
        if (mine !== latest) return;
        step.value = 'packing';
        progress.value = { done, total };
      });
    } catch {
      outcome = { refusal: t('publish.upload.error.packFailed') };
    }
    if (mine !== latest) return;

    step.value = '';
    progress.value = null;
    if ('refusal' in outcome) {
      error.value = outcome.refusal;
      return;
    }
    adopt(outcome.file, outcome.named);
    // After adopt: the file is handed to the field, which answers with a change
    // event, and that would describe the archive as a plain file.
    ready.value = outcome.description;
  }

  /** A folder through the file dialog: the path to a whole folder without dragging (WCAG 2.5.7). */
  async function onFolderChoice(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const chosenFiles = Array.from(input.files!);
    // So that choosing the same folder again is a change again.
    input.value = '';
    await receive(() => readChosenFolder(chosenFiles));
  }

  /**
   * The field itself changed, or was emptied (`null`): that overrules whatever
   * came before, including what is still being packed.
   */
  function noteChoice(file: File | null): void {
    latest += 1;
    step.value = '';
    progress.value = null;
    error.value = null;
    ready.value = file ? fileReady(file) : '';
  }

  return { step, progress, ready, error, receive, onFolderChoice, noteChoice };
}
