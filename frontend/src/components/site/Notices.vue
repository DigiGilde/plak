<script setup lang="ts">
/**
 * A stack of short notifications (nldd-notification). The component places
 * itself in one shared region, so this list can stay where it belongs in the
 * code; no positioning needed.
 */
import { ref } from 'vue';

type Variant = 'neutral' | 'accent' | 'success' | 'warning' | 'critical';

interface Notice {
  id: number;
  variant: Variant;
  text: string;
  detail?: string;
  /** Milliseconds; 0 leaves the notification up until it is dismissed. */
  duration: number;
}

/**
 * 6 s rather than the 10 s nldd-notification keeps by itself. It is the floor
 * of the reading-time rule for toasts ("5 seconds plus one second per 120
 * words, rounded up", Byrne-Haber, Designing Toast Messages for Accessibility):
 * no notification in this app reaches 120 words, so that rule lands on 6 s for
 * all of them.
 */
const DURATION_CONFIRMATION = 6000;

/** 0 = no timer; the user dismisses it themselves. */
const DURATION_UNTIL_DISMISSED = 0;

/**
 * Going shorter is allowed here under WCAG 2.2.1 because a confirmation holds
 * nothing the user needs to finish their task: the result is already in the
 * page itself. An error is such a thing, so it gets no timer.
 * nldd-notification ignores a duration on `critical` anyway; it is stated
 * explicitly because the duration is set per notification. The timer
 * pauses on hover and focus, so a slower reader does not lose the text.
 */
function defaultDuration(variant: Variant): number {
  return variant === 'critical' ? DURATION_UNTIL_DISMISSED : DURATION_CONFIRMATION;
}

const notices = ref<Notice[]>([]);
let nextId = 0;

function notify(
  variant: Variant,
  text: string,
  detail?: string,
  duration: number = defaultDuration(variant),
): void {
  nextId += 1;
  notices.value = [...notices.value, { id: nextId, variant, text, detail, duration }];
}

function close(id: number): void {
  notices.value = notices.value.filter((notice) => notice.id !== id);
}

defineExpose({ notify });
</script>

<template>
  <nldd-notification
    v-for="notice in notices"
    :key="notice.id"
    :variant="notice.variant"
    :text="notice.text"
    :supporting-text="notice.detail"
    :duration="notice.duration"
    @dismiss="close(notice.id)"
  ></nldd-notification>
</template>
