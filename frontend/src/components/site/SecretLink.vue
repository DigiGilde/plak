<script setup lang="ts">
/**
 * The two ways to share one secret link, shown once, right after the key is
 * made: the full link (link plus code in one address) and the link without the
 * code, with the code beside it.
 *
 * Which one fits depends on the channel, not on the site, so both are always
 * there: a chat with people who may all see the page takes the full link, and
 * a wider or less trusted channel takes the link by one route and the code by
 * another. Everything is copyable on its own, the code included.
 */
import { computed, ref } from 'vue';

import { t } from '@/i18n';

const props = defineProps<{
  /** The plaintext `selector.verifier`; it exists only at this moment. */
  value: string;
  /** Address of the site on the content host, ending in a slash. */
  siteUrl: string;
  /** Prefix of the data-testid attributes, so both screens keep their own. */
  prefix: string;
}>();

/** The part before the dot; the value without a dot is the selector itself. */
/* v8 ignore start -- String.split always returns at least one element, so the
 * fallback never actually runs; it is only here to satisfy noUncheckedIndexedAccess. */
const selector = computed(() => props.value.split('.')[0] ?? props.value);
/* v8 ignore stop */
const code = computed(() => props.value.slice(selector.value.length + 1));

const fullLink = computed(() => `${props.siteUrl}?key=${props.value}`);
const linkWithoutCode = computed(() => `${props.siteUrl}?key=${selector.value}`);

/** Empty until something is copied; sits beside the buttons, not in a toast. */
const notice = ref('');

async function copy(text: string, copied: string, fallback: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    notice.value = copied;
  } catch {
    // Copying may fail (no permission, no secure context); everything is
    // visible on screen, so say so instead of staying silent.
    notice.value = fallback;
  }
}
</script>

<template>
  <nldd-container layout="stack" gap="16">
    <nldd-container layout="stack" gap="4">
      <nldd-text size="sm" color="secondary">{{ t('site.secretLink.withCode') }}</nldd-text>
      <p class="address">
        <nldd-link :href="fullLink" target="_blank" :data-testid="`${prefix}-link`">{{
          fullLink
        }}</nldd-link>
      </p>
      <nldd-text size="sm" color="secondary">
        {{ t('site.secretLink.withCode.hint') }}
      </nldd-text>
      <nldd-button
        variant="primary"
        start-icon="copy"
        :text="t('site.secretLink.copyLink')"
        :accessible-label="t('site.secretLink.copyLink.label')"
        type="button"
        :data-testid="`${prefix}-kopieren`"
        @click="
          copy(
            fullLink,
            t('site.secretLink.linkCopied'),
            t('site.secretLink.copyLinkFailed'),
          )
        "
      ></nldd-button>
    </nldd-container>

    <nldd-container layout="stack" gap="4">
      <nldd-text size="sm" color="secondary">{{ t('site.secretLink.withoutCode') }}</nldd-text>
      <p class="address">
        <nldd-link
          :href="linkWithoutCode"
          target="_blank"
          :data-testid="`${prefix}-link-zonder-code`"
          >{{ linkWithoutCode }}</nldd-link
        >
      </p>
      <nldd-text size="sm" color="secondary">{{ t('site.secretLink.code') }}</nldd-text>
      <p class="address code" :data-testid="`${prefix}-code`">{{ code }}</p>
      <nldd-text size="sm" color="secondary">
        {{ t('site.secretLink.withoutCode.hint') }}
      </nldd-text>
      <nldd-button-group orientation="horizontal">
        <nldd-button
          variant="secondary"
          start-icon="copy"
          :text="t('site.secretLink.copyLinkWithoutCode')"
          type="button"
          :data-testid="`${prefix}-kopieren-zonder-code`"
          @click="
            copy(
              linkWithoutCode,
              t('site.secretLink.linkWithoutCodeCopied'),
              t('site.secretLink.copyLinkFailed'),
            )
          "
        ></nldd-button>
        <nldd-button
          variant="secondary"
          start-icon="copy"
          :text="t('site.secretLink.copyCode')"
          type="button"
          :data-testid="`${prefix}-code-kopieren`"
          @click="
            copy(
              code,
              t('site.secretLink.codeCopied'),
              t('site.secretLink.copyCodeFailed'),
            )
          "
        ></nldd-button>
      </nldd-button-group>
    </nldd-container>

    <!-- A status line, not a toast: it belongs to these buttons and has to be
         announced when it appears after a copy too. -->
    <nldd-text
      size="sm"
      color="secondary"
      role="status"
      :data-testid="`${prefix}-melding`"
      >{{ notice }}</nldd-text
    >
  </nldd-container>
</template>

<style scoped>
/* A secret link and its code are long, unbreakable strings; without this they
   run past the margin on a narrow screen. */
.address {
  margin: 0;
  overflow-wrap: anywhere;
}

.code {
  font-family: var(--primitives-font-family-monospace, monospace);
}
</style>
