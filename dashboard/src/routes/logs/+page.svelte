<script module lang="ts">
  export { logListEmptyCopy } from '$lib/modules/logs/presenter';
</script>

<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import LogsPage from '$lib/modules/logs/view/LogsPage.svelte';

  const app = createApp();
</script>

<LogsPage cluster={app.cluster} live={app.live} logs={app.logs} />
