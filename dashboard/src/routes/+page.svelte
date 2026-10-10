<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import ClusterPage from '$lib/modules/cluster/view/ClusterPage.svelte';

  const app = createApp();
</script>

<ClusterPage
  cluster={app.cluster}
  usage={app.usage}
  settings={app.settings}
  run={app.run}
  network={app.network}
/>
