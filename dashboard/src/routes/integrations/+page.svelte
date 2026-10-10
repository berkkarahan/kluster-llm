<script lang="ts">
  import { createApp } from '$lib/app.svelte';
  import IntegrationsPage from '$lib/modules/integrations/view/IntegrationsPage.svelte';

  const app = createApp();
</script>

<IntegrationsPage cluster={app.cluster} integrations={app.integrations} />
