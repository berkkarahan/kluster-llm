<script lang="ts">
  import type { PrimaDeployment } from '../prima-deployment.svelte';
  import type { RunInteractor } from '../interactor.svelte';
  import NextDeploymentView from './NextDeploymentView.svelte';

  let {
    deployment,
    presenter,
    onReview,
  }: {
    deployment: PrimaDeployment;
    presenter: RunInteractor;
    onReview: () => void;
  } = $props();
</script>

<NextDeploymentView {deployment} {presenter} {onReview} />
