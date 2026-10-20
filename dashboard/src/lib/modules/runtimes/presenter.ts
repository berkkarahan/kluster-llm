/** Placement admission is separate from inference qualification. */
export function primaPlacementError(preview: Record<string, any> | null): string {
  if (!preview) return '';
  const errors = preview.placement_errors ??
    (preview.prima_topology === null ? preview.qualification_errors : []);
  return errors?.join('; ') || (preview.prima_topology === null
    ? 'Prima cannot represent this placement. Review the selected devices and inspect again.' : '');
}
