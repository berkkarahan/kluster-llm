<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import RunPage from '$lib/modules/run/view/RunPage.svelte';

  const app = createApp();
</script>

<RunPage presenter={app.run} />
