import type { RuntimeLibrary } from '../runtimes/entities';

export function runtimeRunReason(library: RuntimeLibrary | undefined, master: string,
  plan: Record<string, unknown> | null): string {
  if (!library) return '';
  const engine = library.target?.version.engine === 'primacpp' ? 'Prima' : 'llama.cpp';
  const identity = library.target?.version.runtime_id ?? library.selected;
  if (!identity) return `Choose and prepare a ${engine} version in Settings.`;
  if (plan?.runtime_id && plan.runtime_id !== identity) return `The ${engine} target changed. Refresh the deployment plan.`;
  const required = Array.isArray(plan?.runtime_required_nodes) ? plan.runtime_required_nodes as string[] : [master];
  const missing = required.filter(id => {
    const node = library.nodes?.find(n => n.node_id === id);
    return !node || ['offline', 'upgrade_required'].includes(node.phase) || node.prepared?.runtime_id !== identity;
  });
  return missing.length ? `Prepare the target ${engine} version on ${missing.join(', ')}.` : '';
}
