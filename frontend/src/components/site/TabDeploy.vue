<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';

import * as plak from '@/api/plak';
import { ApiError } from '@/api/client';
import type { Me, RepositoryProvider, SiteRepository } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import ErrorBanner from '@/components/ErrorBanner.vue';
import Notices from '@/components/site/Notices.vue';
import { fetchCurrentMember } from '@/composables/currentMember';
import { type MessageKey, t } from '@/i18n';

/**
 * A sentence cut apart at the placeholders that stand for an element rather
 * than a word, so the inline code and links keep their own tags while the
 * wording stays one catalogue entry. The fragments come back in the order the
 * message names them: one more than there are marks.
 */
function segments(
  key: MessageKey,
  marks: string[],
  values: Record<string, string> = {},
): string[] {
  const filled = t(key, {
    ...values,
    ...Object.fromEntries(marks.map((name, index) => [name, `\u0000${index}\u0000`])),
  });
  return filled.split(/\u0000\d+\u0000/);
}

// See TabOverview.vue for why inheritAttrs is off on every tab.
defineOptions({ inheritAttrs: false });

const props = defineProps<{ group: string; site: string; contentBase: string }>();

const loading = ref(true);
const error = ref<unknown>(null);
const notices = ref<InstanceType<typeof Notices> | null>(null);

// currentMember caches the /me response typed as Member; ciForgejoHosts and
// ciAudience ride along, the same pattern Site.vue and Group.vue use for
// contentBaseUrl.
const me = ref<Me | null>(null);
const repository = ref<SiteRepository | null>(null);

/**
 * Only a site admin may link or unlink a repository (PUT/DELETE on
 * `/repository` is 403 for anyone else); a reader or editor gets a read-only
 * view. Platform admins bypass site roles entirely, same as the backend.
 */
const isSiteAdmin = computed<boolean>(() => {
  if (!me.value) return false;
  if (me.value.platformRole === 'admin') return true;
  const siteRole = me.value.siteRoles.find(
    (r) => r.groupSlug === props.group && r.siteSlug === props.site,
  );
  if (siteRole) return siteRole.effectiveRole === 'admin';
  return me.value.groupRoles.some((r) => r.groupSlug === props.group && r.role === 'admin');
});

const editing = ref(false);
const formProvider = ref<RepositoryProvider>('github');
const formHost = ref('');
const formOwnerRepo = ref('');
const formLiveBranch = ref('');
const formTouched = ref(false);
const formError = ref<string | null>(null);
const formBusy = ref(false);

const unlinkOpen = ref(false);
const unlinkBusy = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const [loggedIn, repo] = await Promise.all([
      fetchCurrentMember(),
      plak.siteRepository(props.group, props.site),
    ]);
    me.value = loggedIn as Me | null;
    repository.value = repo;
  } catch (f) {
    error.value = f;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => [props.group, props.site], load);

function errorText(f: unknown, fallback: string): string {
  return f instanceof ApiError ? (f.problem.detail ?? f.problem.title) : fallback;
}

function inputValue(event: Event): string {
  const detail = (event as CustomEvent<{ value?: string }>).detail;
  return detail?.value ?? (event.target as HTMLInputElement | null)?.value ?? '';
}

const forgejoHosts = computed(() => me.value?.ciForgejoHosts ?? []);

/** The hostname a configured Forgejo origin (e.g. "https://code.overheid.nl") resolves to. */
function hostnameOf(origin: string): string {
  try {
    return new URL(origin).hostname;
  } catch {
    return origin;
  }
}

type RepositoryReference =
  | { kind: 'empty' }
  | { kind: 'invalid'; message: string }
  | {
      kind: 'valid';
      owner: string;
      repo: string;
      /** Set only when the input was a URL or git@ address that named its own host. */
      resolved?: { provider: RepositoryProvider; host?: string; label: string };
    };

/**
 * Accepts "eigenaar/repo", and also a pasted repository URL
 * (https, git@ SSH, with or without ".git", a trailing slash or a
 * "/tree/<branch>" suffix). A URL's host must be github.com or one of
 * `me.ciForgejoHosts`; anything else is reported, not silently ignored.
 */
function parseRepositoryReference(raw: string, hosts: string[]): RepositoryReference {
  const input = raw.trim();
  if (!input) return { kind: 'empty' };

  const allowedHostsText = ['github.com', ...hosts.map(hostnameOf)].join(', ');

  function resolveHost(hostname: string): { provider: RepositoryProvider; host?: string; label: string } | null {
    if (hostname === 'github.com') return { provider: 'github', label: 'GitHub' };
    const match = hosts.find((origin) => hostnameOf(origin) === hostname);
    return match
      ? {
          provider: 'forgejo',
          host: match,
          label: t('publish.deploy.form.forgejoLabel', { host: hostname }),
        }
      : null;
  }

  function fromPath(hostname: string, pathname: string): RepositoryReference {
    const segments = pathname.split('/').filter(Boolean);
    if (segments.length < 2) {
      return { kind: 'invalid', message: t('publish.deploy.form.urlNoOwner') };
    }
    const resolved = resolveHost(hostname);
    if (!resolved) {
      return {
        kind: 'invalid',
        message: t('publish.deploy.form.unknownHost', {
          host: hostname,
          allowed: allowedHostsText,
        }),
      };
    }
    return {
      kind: 'valid',
      owner: segments[0]!,
      repo: segments[1]!.replace(/\.git$/, ''),
      resolved,
    };
  }

  const sshMatch = /^git@([^:]+):(.+)$/.exec(input);
  if (sshMatch) return fromPath(sshMatch[1]!, `/${sshMatch[2]}`);

  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(input)) {
    let url: URL;
    try {
      url = new URL(input);
    } catch {
      return { kind: 'invalid', message: t('publish.deploy.form.invalidUrl') };
    }
    return fromPath(url.hostname, url.pathname);
  }

  if (/^[^\s/]+\/[^\s/]+$/.test(input)) {
    const [owner, repo] = input.split('/') as [string, string];
    return { kind: 'valid', owner, repo };
  }

  return { kind: 'invalid', message: t('publish.deploy.form.invalidReference') };
}

const repositoryReference = computed(() => parseRepositoryReference(formOwnerRepo.value, forgejoHosts.value));

const ownerRepoInvalid = computed(
  () => formTouched.value && repositoryReference.value.kind !== 'valid',
);

const repositoryReferenceError = computed(() => {
  const parsed = repositoryReference.value;
  return parsed.kind === 'invalid' ? parsed.message : null;
});

const recognizedRepository = computed(() => {
  const parsed = repositoryReference.value;
  return parsed.kind === 'valid' && parsed.resolved
    ? t('publish.deploy.form.recognized', {
        label: parsed.resolved.label,
        repo: `${parsed.owner}/${parsed.repo}`,
      })
    : null;
});

// A pasted URL names its own provider/host; follow it so the dropdowns below
// show what was understood, while staying editable as a fallback.
watch(repositoryReference, (parsed) => {
  if (parsed.kind === 'valid' && parsed.resolved) {
    formProvider.value = parsed.resolved.provider;
    if (parsed.resolved.host) formHost.value = parsed.resolved.host;
  }
});

function openLinkForm(): void {
  formProvider.value = 'github';
  formHost.value = forgejoHosts.value[0] ?? '';
  formOwnerRepo.value = '';
  formLiveBranch.value = '';
  formTouched.value = false;
  formError.value = null;
  editing.value = true;
}

function openChangeForm(): void {
  if (!repository.value) return;
  formProvider.value = repository.value.provider;
  formHost.value = repository.value.provider === 'forgejo' ? repository.value.host : (forgejoHosts.value[0] ?? '');
  formOwnerRepo.value = `${repository.value.owner}/${repository.value.repo}`;
  formLiveBranch.value = repository.value.liveBranch ?? '';
  formTouched.value = false;
  formError.value = null;
  editing.value = true;
}

function closeForm(): void {
  editing.value = false;
  formError.value = null;
}

watch(formProvider, (provider) => {
  if (provider === 'forgejo' && !formHost.value) {
    formHost.value = forgejoHosts.value[0] ?? '';
  }
});

async function submitRepository(): Promise<void> {
  formError.value = null;
  formTouched.value = true;
  const parsed = repositoryReference.value;
  if (parsed.kind !== 'valid') return;
  formBusy.value = true;
  try {
    const result = await plak.setSiteRepository(props.group, props.site, {
      provider: formProvider.value,
      host: formProvider.value === 'forgejo' ? formHost.value : undefined,
      owner: parsed.owner,
      repo: parsed.repo,
      liveBranch: formLiveBranch.value.trim() === '' ? null : formLiveBranch.value.trim(),
    });
    repository.value = result;
    editing.value = false;
    notices.value?.notify(
      'success',
      t('publish.deploy.repo.linked'),
      t('publish.deploy.repo.linkedDetail', { repo: `${result.owner}/${result.repo}` }),
    );
  } catch (f) {
    formError.value = errorText(f, t('publish.deploy.repo.linkFailed'));
  } finally {
    formBusy.value = false;
  }
}

async function unlinkRepository(): Promise<void> {
  unlinkBusy.value = true;
  try {
    await plak.deleteSiteRepository(props.group, props.site);
    repository.value = null;
    unlinkOpen.value = false;
  } catch (f) {
    // The modal sits in the top layer and renders the page below it inert; a
    // notification there would be unreachable. So close first, then notify.
    unlinkOpen.value = false;
    notices.value?.notify(
      'critical',
      t('publish.deploy.repo.unlinkFailed'),
      errorText(f, t('publish.deploy.repo.unlinkFailedDetail')),
    );
  } finally {
    unlinkBusy.value = false;
  }
}

const providerLabel = (provider: RepositoryProvider): string =>
  provider === 'github' ? 'GitHub' : 'Forgejo';

// The snippets upload to the admin API, so `host` is deliberately the CI
// audience from /me, not the content host where the site itself lives.
const host = computed(() => me.value?.ciAudience ?? window.location.origin);
const siteRef = computed(() => `${props.group}/${props.site}`);
const liveBranchOrMain = computed(() => repository.value?.liveBranch ?? 'main');

// Matches docs/publishing.md: always pin the action to a commit SHA, never
// to a tag or branch.
const githubSnippet = computed(
  () => `name: Publiceer

on:
  push:
    branches: [${liveBranchOrMain.value}]
  pull_request:
    types: [opened, synchronize, reopened, closed]

permissions:
  contents: read
  id-token: write

jobs:
  publiceer:
    if: github.event.action != 'closed'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<commit-sha>

      - name: Build site
        run: |
          npm ci
          npm run build

      - name: Publiceer live
        if: github.event_name == 'push'
        uses: minbzk/plak/actions/publiceer@<commit-sha>
        with:
          host: ${host.value}
          site: ${siteRef.value}
          dist-path: ./dist

      - name: Publiceer preview
        if: github.event_name == 'pull_request'
        uses: minbzk/plak/actions/publiceer@<commit-sha>
        with:
          host: ${host.value}
          site: ${siteRef.value}
          dist-path: ./dist
          preview-ref: pr-\${{ github.event.pull_request.number }}

  teardown:
    if: github.event_name == 'pull_request' && github.event.action == 'closed'
    runs-on: ubuntu-latest
    steps:
      - name: Ruim preview op
        uses: minbzk/plak/actions/publiceer@<commit-sha>
        with:
          host: ${host.value}
          site: ${siteRef.value}
          preview-ref: pr-\${{ github.event.pull_request.number }}
          teardown: "true"
`,
);

const forgejoSnippet = computed(
  () => `name: Publiceer

on:
  push:
    branches: [${liveBranchOrMain.value}]
  pull_request:
    types: [opened, synchronize, reopened, closed]

permissions:
  contents: read
  id-token: write

jobs:
  publiceer:
    if: github.event.action != 'closed'
    runs-on: docker # de runner-label van deze Forgejo-instantie
    enable-openid-connect: true
    steps:
      - uses: actions/checkout@<commit-sha>

      - name: Build site
        run: |
          npm ci
          npm run build

      - name: Publiceer live
        if: github.event_name == 'push'
        uses: https://github.com/minbzk/plak/actions/publiceer@<commit-sha>
        with:
          host: ${host.value}
          site: ${siteRef.value}
          dist-path: ./dist

      - name: Publiceer preview
        if: github.event_name == 'pull_request'
        uses: https://github.com/minbzk/plak/actions/publiceer@<commit-sha>
        with:
          host: ${host.value}
          site: ${siteRef.value}
          dist-path: ./dist
          preview-ref: pr-\${{ github.event.pull_request.number }}

  teardown:
    if: github.event_name == 'pull_request' && github.event.action == 'closed'
    runs-on: docker # de runner-label van deze Forgejo-instantie
    enable-openid-connect: true
    steps:
      - name: Ruim preview op
        uses: https://github.com/minbzk/plak/actions/publiceer@<commit-sha>
        with:
          host: ${host.value}
          site: ${siteRef.value}
          preview-ref: pr-\${{ github.event.pull_request.number }}
          teardown: "true"
`,
);

const workflowSnippet = computed(() =>
  repository.value?.provider === 'forgejo' ? forgejoSnippet.value : githubSnippet.value,
);

const workflowHint = computed(() =>
  segments('publish.deploy.workflow.hint', ['code'], {
    provider: providerLabel(repository.value?.provider ?? 'github'),
  }),
);

const cliInstall = computed(() =>
  segments('publish.deploy.cli.install', ['repo', 'folder', 'install', 'upgrade', 'run']),
);

const cliLogin = computed(() =>
  segments('publish.deploy.cli.login', ['login', 'link', 'publish']),
);

const cliSnippet = computed(
  () => `plak login --host ${host.value}

# Live: vervangt direct wat bezoekers zien
plak publish ./dist --host ${host.value} --site ${siteRef.value}

# Preview: eigen pad, laat de live site ongemoeid
plak publish ./dist --host ${host.value} --site ${siteRef.value} --preview pr-42

plak logout
`,
);
</script>

<template>
  <Notices ref="notices" />

  <nldd-activity-indicator
    v-if="loading"
    :text="t('publish.deploy.loading')"
  ></nldd-activity-indicator>

  <ErrorBanner v-else-if="error" :error="error" />

  <nldd-container v-else layout="stack" gap="24">
    <section aria-labelledby="kop-repository">
      <nldd-container layout="stack" gap="8">
        <nldd-title :size="4">
          <h2 id="kop-repository">{{ t('publish.deploy.heading') }}</h2>
          <span slot="subtitle">{{ t('publish.deploy.intro') }}</span>
        </nldd-title>

        <nldd-container layout="stack" gap="16">
          <template v-if="!editing">
            <template v-if="repository">
              <nldd-box>
                <nldd-container layout="stack" gap="8" padding="16">
                  <nldd-text data-testid="repository-naam">
                    {{ providerLabel(repository.provider) }} -
                    {{ repository.owner }}/{{ repository.repo }}
                  </nldd-text>
                  <nldd-text size="sm" color="secondary">
                    {{ repository.host }}
                  </nldd-text>
                  <nldd-text size="sm" color="secondary" data-testid="repository-livebranch">
                    {{
                      t('publish.deploy.repo.liveBranch', {
                        branch: repository.liveBranch ?? t('publish.deploy.repo.anyBranch'),
                      })
                    }}
                  </nldd-text>
                  <nldd-text size="sm" color="secondary">
                    {{
                      t('publish.deploy.repo.linkedBy', {
                        who: repository.createdBy || t('publish.deploy.repo.unknownWho'),
                      })
                    }}
                  </nldd-text>
                </nldd-container>
              </nldd-box>
              <nldd-button-group v-if="isSiteAdmin" orientation="horizontal">
                <nldd-button
                  variant="secondary"
                  :text="t('publish.deploy.repo.change')"
                  data-testid="repository-wijzigen"
                  @click="openChangeForm"
                ></nldd-button>
                <nldd-button
                  variant="critical-transparent"
                  :text="t('publish.deploy.repo.unlink')"
                  data-testid="repository-ontkoppelen"
                  @click="unlinkOpen = true"
                ></nldd-button>
              </nldd-button-group>
            </template>
            <template v-else-if="isSiteAdmin">
              <nldd-inline-dialog
                icon="link"
                :text="t('publish.deploy.repo.empty')"
                :supporting-text="t('publish.deploy.repo.emptyAdmin')"
                data-testid="repository-leeg"
              ></nldd-inline-dialog>
              <nldd-button
                variant="primary"
                :text="t('publish.deploy.repo.link')"
                width="fit-content"
                data-testid="repository-koppelen"
                @click="openLinkForm"
              ></nldd-button>
            </template>
            <template v-else>
              <nldd-inline-dialog
                icon="link"
                :text="t('publish.deploy.repo.empty')"
                :supporting-text="t('publish.deploy.repo.emptyReader')"
                data-testid="repository-leeg"
              ></nldd-inline-dialog>
            </template>
          </template>

          <nldd-form v-else-if="isSiteAdmin" data-testid="repository-formulier" @submit.prevent="submitRepository">
            <nldd-form-field :label="t('publish.deploy.form.provider')">
              <nldd-dropdown width="180px">
                <select
                  :value="formProvider"
                  :aria-label="t('publish.deploy.form.provider')"
                  data-testid="repository-provider"
                  @change="formProvider = ($event.target as HTMLSelectElement).value as RepositoryProvider"
                >
                  <option value="github">GitHub</option>
                  <option value="forgejo">Forgejo</option>
                </select>
              </nldd-dropdown>
            </nldd-form-field>

            <nldd-form-field
              v-if="formProvider === 'forgejo'"
              :label="t('publish.deploy.form.host')"
            >
              <nldd-dropdown width="20rem">
                <select
                  :value="formHost"
                  :aria-label="t('publish.deploy.form.host')"
                  data-testid="repository-host"
                  @change="formHost = ($event.target as HTMLSelectElement).value"
                >
                  <option v-for="candidate in forgejoHosts" :key="candidate" :value="candidate">
                    {{ candidate }}
                  </option>
                </select>
              </nldd-dropdown>
            </nldd-form-field>

            <nldd-form-field :label="t('publish.deploy.form.repo')">
              <nldd-text-field
                name="repository-eigenaar-repo"
                width="20rem"
                :placeholder="t('publish.deploy.form.repoPlaceholder')"
                required
                :value="formOwnerRepo"
                :invalid="ownerRepoInvalid || formError !== null || undefined"
                :unmet="
                  repositoryReferenceError !== null
                    ? 'repository-reference-ongeldig'
                    : formError !== null
                      ? 'repository-server'
                      : undefined
                "
                data-testid="repository-eigenaar-repo"
                @input="formOwnerRepo = inputValue($event)"
              ></nldd-text-field>
              <nldd-text v-if="recognizedRepository" size="sm" color="secondary" data-testid="repository-herkend">
                {{ recognizedRepository }}
              </nldd-text>
              <nldd-form-field-help-text>
                {{ t('publish.deploy.form.repoHelp') }}
              </nldd-form-field-help-text>
              <nldd-validation-list>
                <nldd-validation-item id="repository-eigenaar-repo-vereist" required>
                  {{ t('publish.deploy.form.repoRequired') }}
                </nldd-validation-item>
                <nldd-validation-item id="repository-reference-ongeldig">
                  {{ repositoryReferenceError }}
                </nldd-validation-item>
                <nldd-validation-item id="repository-server">
                  {{ formError }}
                </nldd-validation-item>
              </nldd-validation-list>
            </nldd-form-field>

            <nldd-form-field :label="t('publish.deploy.form.branch')" optional>
              <nldd-text-field
                name="repository-livebranch"
                width="20rem"
                :placeholder="t('publish.deploy.form.branchPlaceholder')"
                :value="formLiveBranch"
                data-testid="repository-livebranch-invoer"
                @input="formLiveBranch = inputValue($event)"
              ></nldd-text-field>
              <nldd-form-field-help-text>
                {{ t('publish.deploy.form.branchHelp') }}
              </nldd-form-field-help-text>
            </nldd-form-field>

            <nldd-form-actions>
              <nldd-button-group>
                <nldd-button
                  variant="secondary"
                  :text="t('publish.deploy.form.cancel')"
                  type="button"
                  data-testid="repository-annuleren"
                  @click="closeForm"
                ></nldd-button>
                <nldd-button
                  variant="primary"
                  type="submit"
                  :text="t('publish.deploy.form.submit')"
                  :loading="formBusy || undefined"
                  data-testid="repository-opslaan"
                ></nldd-button>
              </nldd-button-group>
            </nldd-form-actions>
          </nldd-form>

          <nldd-rich-text v-if="repository">
            <p>{{ workflowHint[0] }}<code>&lt;commit-sha&gt;</code>{{ workflowHint[1] }}</p>
            <nldd-code-viewer language="yaml" data-testid="workflow-snippet">{{
              workflowSnippet
            }}</nldd-code-viewer>
          </nldd-rich-text>
        </nldd-container>
      </nldd-container>
    </section>

    <section aria-labelledby="kop-cli">
      <nldd-container layout="stack" gap="8">
        <nldd-title :size="4">
          <h2 id="kop-cli">{{ t('publish.deploy.cli.heading') }}</h2>
        </nldd-title>
        <nldd-rich-text>
          <p>
            {{ cliInstall[0]
            }}<nldd-link href="https://github.com/minbzk/plak" data-testid="plak-cli-repo-link"
              >github.com/minbzk/plak</nldd-link
            >{{ cliInstall[1] }}<code>cli/</code>{{ cliInstall[2]
            }}<code>uv tool install "git+https://github.com/minbzk/plak#subdirectory=cli"</code
            >{{ cliInstall[3] }}<code>uv tool upgrade plak</code>{{ cliInstall[4]
            }}<code>uv run --project cli plak ...</code>{{ cliInstall[5] }}
          </p>
          <p>
            {{ cliLogin[0] }}<code>plak login</code>{{ cliLogin[1]
            }}<nldd-link href="/cli-koppelen" data-testid="cli-koppelen-link">/cli-koppelen</nldd-link
            >{{ cliLogin[2] }}<code>plak publish</code>{{ cliLogin[3] }}
          </p>
          <nldd-code-viewer language="bash" data-testid="cli-snippet">{{ cliSnippet }}</nldd-code-viewer>
        </nldd-rich-text>
      </nldd-container>
    </section>
  </nldd-container>

  <ConfirmModal
    :open="unlinkOpen"
    :title="t('publish.deploy.unlink.title')"
    :text="t('publish.deploy.unlink.text')"
    :keep-label="t('publish.deploy.unlink.keep')"
    :confirm-label="t('publish.deploy.unlink.confirm')"
    :busy="unlinkBusy"
    @confirm="unlinkRepository"
    @close="unlinkOpen = false"
  />
</template>
