export type CommandShell = 'bash' | 'powershell';

export function shellQuote(value: string, shell: CommandShell): string {
  return shell === 'powershell' ? `'${value.replaceAll("'", "''")}'`
    : `'${value.replaceAll("'", "'\\''")}'`;
}

export function connectCommand(
  agent: string,
  model: string,
  url: string,
  token: string,
  shell: CommandShell,
  noLaunch = false,
  redactToken = false
): string {
  if (!model || !url || !token) return '';
  const credential = shellQuote(redactToken ? '••••' : token, shell);
  const command = `kllm connect ${agent} --url ${shellQuote(url, shell)} --model ${shellQuote(model, shell)} --no-serve${noLaunch ? ' --no-launch' : ''}`;
  return shell === 'powershell'
    ? `$env:KLLM_API_KEY = ${credential}; ${command}`
    : `KLLM_API_KEY=${credential} ${command}`;
}
