export type CommandShell = 'bash' | 'powershell';

export function shellQuote(value: string, shell: CommandShell): string {
  return shell === 'powershell' ? `'${value.replaceAll("'", "''")}'`
    : `'${value.replaceAll("'", "'\\''")}'`;
}

export function connectCommand(agent: string, model: string, url: string, shell: CommandShell, noLaunch = false): string {
  if (!model || !url) return '';
  return `kllm connect ${agent} --url ${shellQuote(url, shell)} --model ${shellQuote(model, shell)}${noLaunch ? ' --no-launch' : ''}`;
}
