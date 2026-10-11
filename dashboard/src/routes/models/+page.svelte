<script lang="ts">
  import { createApp } from '#lib/app.svelte';
  import ModelsPage from '#lib/modules/models/view/ModelsPage.svelte';

  const app = createApp();
</script>

<ModelsPage models={app.models} run={app.run} cluster={app.cluster} />
