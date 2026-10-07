<script setup lang="ts">
// Privacy statement: who is responsible, what Plak keeps, for how long, who
// can see it and what someone can ask of us.
import PlatformPage from './PlatformPage.vue';
import { t } from '@/i18n';
</script>

<template>
  <PlatformPage :title="t('page.privacy.title')">
    <p>{{ t('page.privacy.intro') }}</p>
    <p>
      <strong>{{ t('page.privacy.draft') }}</strong>
    </p>
    <h2>{{ t('page.privacy.controller.heading') }}</h2>
    <p>{{ t('page.privacy.controller.body') }}</p>
    <h2>{{ t('page.privacy.data.heading') }}</h2>
    <ul>
      <li>{{ t('page.privacy.data.account') }}</li>
      <li>{{ t('page.privacy.data.invitees') }}</li>
      <li>{{ t('page.privacy.data.audit') }}</li>
      <li>{{ t('page.privacy.data.visits') }}</li>
      <li>{{ t('page.privacy.data.content') }}</li>
    </ul>
    <h2>{{ t('page.privacy.purpose.heading') }}</h2>
    <p>{{ t('page.privacy.purpose.body') }}</p>
    <h2>{{ t('page.privacy.processors.heading') }}</h2>
    <p>{{ t('page.privacy.processors.body') }}</p>
    <h2>{{ t('page.privacy.retention.heading') }}</h2>
    <p>{{ t('page.privacy.retention.body') }}</p>
    <p>{{ t('page.privacy.retention.ip') }}</p>
    <p>{{ t('page.privacy.retention.session') }}</p>
    <h2>{{ t('page.privacy.visibility.heading') }}</h2>
    <p>{{ t('page.privacy.visibility.body') }}</p>
    <h2>{{ t('page.privacy.rights.heading') }}</h2>
    <p>{{ t('page.privacy.rights.body') }}</p>
    <p>
      {{ t('page.privacy.rights.request.before')
      }}<a href="https://www.rijksoverheid.nl/contact/contactformulier">{{
        t('page.privacy.rights.request.link')
      }}</a
      >{{ t('page.privacy.rights.request.after') }}
      {{ t('page.privacy.rights.register.before')
      }}<a href="https://www.avgregisterrijksoverheid.nl">{{
        t('page.privacy.rights.register.link')
      }}</a
      >{{ t('page.privacy.rights.register.after') }}
    </p>
    <h2>{{ t('page.privacy.fg.heading') }}</h2>
    <p>
      {{ t('page.privacy.fg.before')
      }}<a href="mailto:postbusfg@minbzk.nl">postbusfg@minbzk.nl</a
      >{{ t('page.privacy.fg.after') }}
    </p>
    <h2>{{ t('page.privacy.complaint.heading') }}</h2>
    <p>
      {{ t('page.privacy.complaint.before')
      }}<a href="https://www.autoriteitpersoonsgegevens.nl">{{
        t('page.privacy.complaint.link')
      }}</a
      >{{ t('page.privacy.complaint.after') }}
    </p>
    <h2>{{ t('page.privacy.contact.heading') }}</h2>
    <p>
      {{ t('page.privacy.contact.before')
      }}<a href="mailto:digigilde@rijksoverheid.nl">digigilde@rijksoverheid.nl</a
      >{{ t('page.privacy.contact.after') }}
    </p>
  </PlatformPage>
</template>
