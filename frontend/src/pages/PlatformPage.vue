<script setup lang="ts">
// Shared layout for the publicly accessible platform pages:
// privacy, accessibility, about. No login required.
import { onMounted, watch } from 'vue';
import { useRoute } from 'vue-router';

import { setBreadcrumbs } from '../composables/breadcrumbs';
import { t } from '@/i18n';

const props = withDefaults(defineProps<{ title: string }>(), {});
const route = useRoute();

function setCrumbs(): void {
  setBreadcrumbs(route.path, [{ text: t('nav.overview'), href: '/' }, { text: props.title }]);
}

onMounted(setCrumbs);
watch(() => [route.path, props.title], setCrumbs);
</script>

<template>
  <nldd-simple-section class="leesbreedte">
    <nldd-title :size="1"><h1>{{ title }}</h1></nldd-title>
    <nldd-spacer size="16"></nldd-spacer>
    <nldd-rich-text>
      <slot />
    </nldd-rich-text>
  </nldd-simple-section>
</template>
