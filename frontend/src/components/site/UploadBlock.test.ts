import { mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FakeDataTransfer, giveFieldAnInput } from './testHelpers';
import UploadBlock from './UploadBlock.vue';

function render(
  props: Partial<InstanceType<typeof UploadBlock>['$props']> = {},
  slots: Record<string, string> = {},
) {
  return mount(UploadBlock, {
    props: {
      required: true,
      fileName: '',
      step: '',
      progress: null,
      ready: '',
      error: null,
      ...props,
    },
    slots,
  });
}

describe('UploadBlock', () => {
  it('holds everything in the one dashed zone', () => {
    const wrapper = render({ ready: 'Klaar.' });
    const zone = wrapper.find('[data-testid="dropzone"]');

    expect(wrapper.element).toBe(zone.element);
    expect(zone.find('nldd-form-field nldd-file-field').exists()).toBe(true);
    expect(zone.find('nldd-form-field nldd-validation-list').exists()).toBe(true);
    expect(zone.find('nldd-form-field nldd-form-field-help-text').text()).toContain('.zip');
    expect(zone.find('[data-testid="drag-done"]').exists()).toBe(true);
    expect(zone.find('nldd-form-actions > nldd-button-group > nldd-button').exists()).toBe(true);
    expect(zone.find('input[type="file"][webkitdirectory][hidden]').exists()).toBe(true);
  });

  it('renders no form of its own, so it can sit inside the form of the sheet', () => {
    const wrapper = render();

    expect(wrapper.find('nldd-form').exists()).toBe(false);
    expect(wrapper.find('form').exists()).toBe(false);
  });

  it('marks the field as one that takes a folder too', () => {
    const wrapper = render();

    expect(wrapper.find('nldd-file-field').attributes()).toHaveProperty('data-takes-folders');
  });

  it('requires a file only while told to', async () => {
    const wrapper = render({ required: true });
    const field = wrapper.find('nldd-file-field');
    expect(field.attributes('required')).toBeDefined();

    await wrapper.setProps({ required: false });

    expect(field.attributes('required')).toBeUndefined();
  });

  it('marks the field invalid only while told to', async () => {
    const wrapper = render();
    const field = wrapper.find('nldd-file-field');
    expect(field.attributes('invalid')).toBeUndefined();

    await wrapper.setProps({ invalid: true });
    expect(field.attributes('invalid')).toBeDefined();

    await wrapper.setProps({ invalid: false });
    expect(field.attributes('invalid')).toBeUndefined();
  });

  it('gives the validation list the file name to check against', () => {
    const wrapper = render({ fileName: 'site.zip' });
    const list = wrapper.find('nldd-validation-list').element as HTMLElement & { value?: string };

    expect(list.getAttribute('value') ?? list.value).toBe('site.zip');
  });

  it('lights up only while something is over the drop target', async () => {
    const wrapper = render();
    const zone = wrapper.find('[data-testid="dropzone"]');
    expect(zone.classes()).not.toContain('dropzone--active');

    await wrapper.setProps({ active: true });

    expect(zone.classes()).toContain('dropzone--active');
  });

  it('puts the actions in the group beside the folder button, after it', () => {
    const wrapper = render({}, { actions: '<nldd-button data-testid="submit"></nldd-button>' });

    const buttons = wrapper.findAll('nldd-button-group > nldd-button');
    expect(buttons.map((button) => button.attributes('data-testid'))).toEqual([
      'upload-folder',
      'submit',
    ]);
  });

  it('puts what the chosen slot holds right after the field', () => {
    const wrapper = render({}, { chosen: '<nldd-token data-testid="chip"></nldd-token>' });

    expect(wrapper.find('nldd-file-field').element.nextElementSibling?.getAttribute('data-testid')).toBe(
      'chip',
    );
  });

  it('opens the folder dialog from the button, which must not submit a form around it', async () => {
    const wrapper = render();
    const input = wrapper.find('[data-testid="upload-folder-input"]').element as HTMLInputElement;
    const click = vi.spyOn(input, 'click');
    const button = wrapper.find('[data-testid="upload-folder"]');

    await button.trigger('click');

    expect(click).toHaveBeenCalledOnce();
    expect(button.attributes('type')).toBe('button');
  });

  it('reports a choice in the field and a chosen folder as two different things', () => {
    const wrapper = render();

    wrapper.find('nldd-file-field').element.dispatchEvent(new Event('change'));
    expect(wrapper.emitted('choice')).toHaveLength(1);
    expect(wrapper.emitted('folder')).toBeUndefined();

    wrapper.find('[data-testid="upload-folder-input"]').element.dispatchEvent(new Event('change'));
    expect(wrapper.emitted('choice')).toHaveLength(1);
    expect(wrapper.emitted('folder')).toHaveLength(1);
  });

  it('hands whoever uses it the field', () => {
    const wrapper = render();

    expect((wrapper.vm as unknown as { field: HTMLElement }).field).toBe(
      wrapper.find('nldd-file-field').element,
    );
  });
});

describe('UploadBlock: the file in the field', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  interface Exposed {
    handOver: (file: File) => boolean;
    clearField: () => void;
  }

  function site(): File {
    return new File(['pk'], 'site.tar.gz');
  }

  function withInput() {
    const wrapper = render();
    const field = wrapper.find('nldd-file-field').element;
    return { wrapper, field, input: giveFieldAnInput(field), block: wrapper.vm as unknown as Exposed };
  }

  it('hands a file to the input in the shadow root, and hears the field answer', () => {
    vi.stubGlobal('DataTransfer', FakeDataTransfer);
    const { wrapper, input, block } = withInput();

    expect(block.handOver(site())).toBe(true);

    expect(input.files?.[0]?.name).toBe('site.tar.gz');
    expect(wrapper.emitted('choice')).toHaveLength(1);
  });

  it('finds an input in the light DOM too', () => {
    vi.stubGlobal('DataTransfer', FakeDataTransfer);
    const wrapper = render();
    const input = document.createElement('input');
    input.type = 'file';
    Object.defineProperty(input, 'files', { value: [site()], writable: true });
    wrapper.find('nldd-file-field').element.append(input);

    expect((wrapper.vm as unknown as Exposed).handOver(site())).toBe(true);
  });

  it('says it did not work when the field has no input yet', () => {
    vi.stubGlobal('DataTransfer', FakeDataTransfer);
    const wrapper = render();

    expect((wrapper.vm as unknown as Exposed).handOver(site())).toBe(false);
    expect(wrapper.emitted('choice')).toBeUndefined();
  });

  it('says it did not work in a browser without a DataTransfer to build with', () => {
    vi.stubGlobal('DataTransfer', undefined);
    const { wrapper, block } = withInput();

    expect(block.handOver(site())).toBe(false);
    expect(wrapper.emitted('choice')).toBeUndefined();
  });

  it('says it did not work when building the FileList throws', () => {
    vi.stubGlobal(
      'DataTransfer',
      class {
        constructor() {
          throw new Error('not allowed');
        }
      },
    );
    const { wrapper, block } = withInput();

    expect(block.handOver(site())).toBe(false);
    expect(wrapper.emitted('choice')).toBeUndefined();
  });

  it('says it did not work when the input does not take the file', () => {
    vi.stubGlobal(
      'DataTransfer',
      class {
        items = { add: () => undefined };
        files = [];
      },
    );
    const { wrapper, block } = withInput();

    expect(block.handOver(site())).toBe(false);
    expect(wrapper.emitted('choice')).toBeUndefined();
  });

  it('empties a field that holds a file, and tells the form', () => {
    vi.stubGlobal('DataTransfer', FakeDataTransfer);
    const { wrapper, input, block } = withInput();
    block.handOver(site());
    const told = vi.fn();
    input.addEventListener('input', told);

    block.clearField();

    expect(input.files).toHaveLength(0);
    expect(wrapper.emitted('choice')).toHaveLength(2);
    expect(told).toHaveBeenCalledOnce();
  });

  it('says nothing when asked to empty a field that holds nothing', () => {
    const { wrapper, block } = withInput();

    block.clearField();

    expect(wrapper.emitted('choice')).toBeUndefined();
  });

  it('asks nothing of a field without an input', () => {
    const wrapper = render();

    expect(() => (wrapper.vm as unknown as Exposed).clearField()).not.toThrow();
    expect(wrapper.emitted('choice')).toBeUndefined();
  });
});
