import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';

import UploadZone from './UploadZone.vue';
import { makeTransfer, untilQuiet, fireDetailEvent, fireDrop } from './testHelpers';

function archive(): File {
  return new File(['pk'], 'site.zip', { type: 'application/zip' });
}

function file(name: string, content = 'x'): File {
  return new File([content], name);
}

async function mountComponent() {
  // Not attached to the document: jsdom blows up on the ARIA reflectors of
  // ElementInternals as soon as nldd-file-field is really connected, and the
  // drag handling here hangs off the component and off `document`, not off the
  // place in the tree.
  const wrapper = mount(UploadZone);
  await flushPromises();
  const field = wrapper.find('[data-testid="upload-invoer"]').element;
  const zone = wrapper.find('[data-testid="sleepzone"]').element;
  return { wrapper, field, zone };
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('UploadZone: choosing via the field', () => {
  it('removes the invalid marker as soon as a file is chosen', async () => {
    const { wrapper, field } = await mountComponent();
    // The way nldd-form marks the field on a submit without a file.
    field.setAttribute('invalid', '');

    fireDetailEvent(field, 'change', { files: [archive()] });
    await flushPromises();

    expect(field.hasAttribute('invalid')).toBe(false);

    wrapper.unmount();
  });

  it('leaves the marker in place when the choice is cleared again', async () => {
    const { wrapper, field } = await mountComponent();
    field.setAttribute('invalid', '');

    fireDetailEvent(field, 'change', { files: [] });
    await flushPromises();

    expect(field.hasAttribute('invalid')).toBe(true);
    expect(wrapper.find('[data-testid="sleep-klaar"]').exists()).toBe(false);

    wrapper.unmount();
  });

  it('gives the validation list the filename as the value to check against', async () => {
    const { wrapper, field } = await mountComponent();
    const list = wrapper.find('nldd-validation-list').element as HTMLElement & { value?: string };

    expect(list.value).toBe('');

    fireDetailEvent(field, 'change', { files: [archive()] });
    await flushPromises();

    expect(list.value).toBe('site.zip');

    wrapper.unmount();
  });

  it('requires a file until there is one, then releases that requirement', async () => {
    const { wrapper, field } = await mountComponent();
    const control = field as HTMLElement & { required?: boolean };

    // nldd-file-field only knows its own FileList, so with `required` on it a
    // dropped file never passes the form's validation.
    expect(control.required).toBe(true);

    fireDetailEvent(field, 'change', { files: [archive()] });
    await flushPromises();

    expect(control.required).toBeFalsy();

    wrapper.unmount();
  });

  it('reads the choice from the element itself when the event carries no detail', async () => {
    const { wrapper, field } = await mountComponent();

    fireDetailEvent(field, 'change', { files: [archive()] });
    await flushPromises();
    expect(wrapper.find('[data-testid="sleep-klaar"]').exists()).toBe(true);

    // Without a detail the field's own FileList is the source, and that is empty.
    field.dispatchEvent(new Event('change'));
    await flushPromises();

    expect(wrapper.find('[data-testid="sleep-klaar"]').exists()).toBe(false);

    wrapper.unmount();
  });

  it('passes the chosen file on when the form is submitted', async () => {
    const { wrapper, field } = await mountComponent();
    const chosen = archive();

    fireDetailEvent(field, 'change', { files: [chosen] });
    await wrapper.find('[data-testid="upload-formulier"]').trigger('submit');

    expect(wrapper.emitted('file')).toEqual([[chosen]]);

    wrapper.unmount();
  });

  it('emits nothing when no file is chosen', async () => {
    const { wrapper } = await mountComponent();

    await wrapper.find('[data-testid="upload-formulier"]').trigger('submit');

    expect(wrapper.emitted('file')).toBeUndefined();

    wrapper.unmount();
  });

  it("says upfront what's allowed, instead of only after a failed attempt", async () => {
    const { wrapper } = await mountComponent();
    const hint = wrapper.find('nldd-form-field-help-text');

    expect(hint.exists()).toBe(true);
    expect(hint.text()).toContain('.zip');
    expect(hint.text()).toContain('.tar.gz');
    expect(hint.text()).toContain('startpagina');
    expect(wrapper.find('nldd-form-field').attributes('supporting-label')).toContain('Sleep');

    wrapper.unmount();
  });
});

describe('UploadZone: dragging', () => {
  it('marks the zone as long as something hovers over it', async () => {
    const { wrapper, zone } = await mountComponent();

    fireDrop(zone, 'dragenter');
    fireDrop(zone, 'dragenter'); // on a child too
    await flushPromises();
    expect(zone.classList.contains('dropzone--active')).toBe(true);

    fireDrop(zone, 'dragleave');
    await flushPromises();
    expect(zone.classList.contains('dropzone--active')).toBe(true);

    fireDrop(zone, 'dragleave');
    await flushPromises();
    expect(zone.classList.contains('dropzone--active')).toBe(false);

    wrapper.unmount();
  });

  it('sets the drag effect to copy, so the cursor tells what will happen', async () => {
    const { wrapper, zone } = await mountComponent();
    const transfer = makeTransfer({ 'site.zip': archive() });

    fireDrop(zone, 'dragover', transfer);

    expect(transfer.dropEffect).toBe('copy');

    wrapper.unmount();
  });

  it('accepts a dragged archive and publishes it via the button', async () => {
    const { wrapper, zone } = await mountComponent();
    const dropped = archive();

    fireDrop(zone, 'drop', makeTransfer({ 'site.zip': dropped }));
    await untilQuiet();

    expect(wrapper.find('[data-testid="sleep-klaar"]').attributes('supporting-text')).toContain(
      'site.zip',
    );

    await wrapper.find('[data-testid="upload-formulier"]').trigger('submit');
    expect(wrapper.emitted('file')).toEqual([[dropped]]);

    wrapper.unmount();
  });

  it('packs a dragged folder into an archive the API can handle', async () => {
    const { wrapper, zone } = await mountComponent();

    fireDrop(
      zone,
      'drop',
      makeTransfer({
        'mijn-site': {
          'index.html': file('index.html', '<h1>hoi</h1>'),
          '.DS_Store': file('.DS_Store'),
          assets: { 'stijl.css': file('stijl.css') },
        },
      }),
    );
    await untilQuiet();

    const notice = wrapper.find('[data-testid="sleep-klaar"]').attributes('supporting-text');
    expect(notice).toContain('mijn-site');
    expect(notice).toContain('2 bestanden');

    await wrapper.find('[data-testid="upload-formulier"]').trigger('submit');
    const sent = wrapper.emitted('file')?.[0]?.[0] as File;
    expect(sent.name).toBe('mijn-site.tar.gz');
    expect(sent.size).toBeGreaterThan(0);

    wrapper.unmount();
  });

  it("refuses before submitting when there's no start page in the folder", async () => {
    const { wrapper, zone } = await mountComponent();

    fireDrop(
      zone,
      'drop',
      makeTransfer({
        'mijn-site': {
          'lees-mij.txt': file('lees-mij.txt'),
          dist: { 'index.html': file('index.html') },
        },
      }),
    );
    await untilQuiet();

    expect(wrapper.find('[data-testid="sleep-fout"]').attributes('supporting-text')).toContain(
      'Sleep de map "dist" zelf',
    );
    expect(wrapper.find('[data-testid="sleep-klaar"]').exists()).toBe(false);

    await wrapper.find('[data-testid="upload-formulier"]').trigger('submit');
    expect(wrapper.emitted('file')).toBeUndefined();

    wrapper.unmount();
  });

  it("refuses a single file that can't be turned into a site", async () => {
    const { wrapper, zone } = await mountComponent();

    fireDrop(zone, 'drop', makeTransfer({ 'stijl.css': file('stijl.css') }));
    await untilQuiet();

    expect(wrapper.find('[data-testid="sleep-fout"]').attributes('supporting-text')).toContain(
      'stijl.css',
    );

    wrapper.unmount();
  });

  it('says honestly that this browser cannot pack', async () => {
    vi.stubGlobal('CompressionStream', undefined);
    const { wrapper, zone } = await mountComponent();

    fireDrop(zone, 'drop', makeTransfer({ 'mijn-site': { 'index.html': file('index.html') } }));
    await untilQuiet();

    expect(wrapper.find('[data-testid="sleep-fout"]').attributes('supporting-text')).toContain(
      'Deze browser kan een map niet zelf inpakken',
    );

    wrapper.unmount();
  });

  it('reports it when packing itself goes wrong', async () => {
    vi.stubGlobal(
      'CompressionStream',
      class {
        constructor() {
          throw new Error('geen geheugen');
        }
      },
    );
    const { wrapper, zone } = await mountComponent();

    fireDrop(zone, 'drop', makeTransfer({ 'mijn-site': { 'index.html': file('index.html') } }));
    await untilQuiet();

    expect(wrapper.find('[data-testid="sleep-fout"]').attributes('supporting-text')).toContain(
      'Inpakken is niet gelukt',
    );
    expect(wrapper.find('[data-testid="sleep-voortgang"]').exists()).toBe(false);

    wrapper.unmount();
  });

  it('does nothing on a drop without content', async () => {
    const { wrapper, zone } = await mountComponent();

    fireDrop(zone, 'drop');
    await untilQuiet(2);

    expect(wrapper.find('[data-testid="sleep-klaar"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="sleep-fout"]').exists()).toBe(false);

    wrapper.unmount();
  });

  it("catches a missed drop, so the admin page doesn't navigate away", async () => {
    const { wrapper } = await mountComponent();

    const missed = fireDrop(document.body, 'drop', makeTransfer({ 'site.zip': archive() }));
    expect(missed.defaultPrevented).toBe(true);
    expect(fireDrop(document.body, 'dragover').defaultPrevented).toBe(true);

    wrapper.unmount();

    // After the cleanup the document should belong to the page itself again.
    expect(fireDrop(document.body, 'drop').defaultPrevented).toBe(false);
  });
});
