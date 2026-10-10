<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import SettingsPage from '$lib/modules/node-settings/view/SettingsPage.svelte';

  const app = createApp();
</script>

<SettingsPage settings={app.settings} cluster={app.cluster} runtimes={app.runtimes} />
