import type { ParameterDescriptor } from './entities';

export const PARAMETER_FIELDS: Record<string, string> = {
  context: 'context', context_auto: 'contextAuto', parallel_slots: 'parallelSlots',
  batch_size: 'batchSize', micro_batch: 'microBatch', threads: 'threads', threads_batch: 'threadsBatch',
  cache_type_k: 'cacheTypeK', cache_type_v: 'cacheTypeV', flash_attention: 'flashAttention',
  mmap_mlock: 'mmapMlock', checkpoints: 'checkpoints', cache_ram: 'cacheRam',
  speculative: 'speculative', draft_model: 'draftModel', draft_max: 'draftMax', moe_offload: 'moeOffload',
  vision: 'vision', mmproj: 'mmproj', chat_template: 'chatTemplate', chat_template_text: 'chatTemplateText',
  temperature: 'temperature', top_p: 'topP', top_k: 'topK', min_p: 'minP',
  presence_penalty: 'presencePenalty', repeat_penalty: 'repeatPenalty', frequency_penalty: 'frequencyPenalty',
  seed: 'seed', max_output_tokens: 'maxOutputTokens', stop: 'stopSequences', enable_thinking: 'enableThinking',
  reasoning_effort: 'reasoningEffort', reasoning_budget: 'reasoningBudget', reasoning_format: 'reasoningFormat',
  device: 'runDevice', gpu_ids: 'runGpuIds', vram_percent: 'runVramPercent', ram_percent: 'runRamPercent',
  placement_strategy: 'placementStrategy',
};

export const PARAMETER_LABELS: Record<string, string> = {
  context: 'Total context capacity', parallel_slots: 'Concurrent requests', max_output_tokens: 'Maximum response tokens',
  batch_size: 'Logical batch size', micro_batch: 'Physical micro-batch', threads: 'Generation threads',
  threads_batch: 'Prompt-processing threads', cache_type_k: 'K cache precision', cache_type_v: 'V cache precision',
  flash_attention: 'Flash Attention', mmap_mlock: 'Model loading', checkpoints: 'Context checkpoints',
  cache_ram: 'Prompt cache RAM (MiB)', seed: 'Random seed', frequency_penalty: 'Frequency penalty',
  temperature: 'Temperature', top_p: 'Top P', top_k: 'Top K', min_p: 'Min P',
  presence_penalty: 'Presence penalty', repeat_penalty: 'Repeat penalty', enable_thinking: 'Thinking',
  reasoning_effort: 'Reasoning effort', reasoning_budget: 'Thinking token budget', reasoning_format: 'Reasoning response format',
  draft_max: 'Maximum draft tokens', moe_offload: 'MoE expert offload', vram_percent: 'Deployment VRAM limit (%)',
  ram_percent: 'Deployment RAM limit (%)', device: 'Compute preference', placement_strategy: 'Placement strategy',
};

/** Free text is an import convenience. JSON argv is the lossless representation. */
export function parseArgumentText(text: string): string[] {
  if (!text.trim()) return [];
  if (text.trim().startsWith('[')) {
    const values: unknown = JSON.parse(text);
    if (!Array.isArray(values) || values.some(value => typeof value !== 'string'))
      throw new Error('Extra arguments must be a JSON array of strings.');
    return values;
  }
  const tokens: string[] = [];
  let token = '', quote = '', started = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === '\\' && quote !== "'" && ['\\', '"', ...(quote ? [] : ["'", ' '])].includes(text[i + 1])) {
      token += text[++i]; started = true;
    } else if (quote) {
      if (char === quote) quote = '';
      else token += char;
    } else if (char === '"' || char === "'") {
      quote = char; started = true;
    } else if (/\s/.test(char)) {
      if (started) tokens.push(token);
      token = ''; started = false;
    } else { token += char; started = true; }
  }
  if (quote) throw new Error('Close the quote in Extra arguments.');
  if (started) tokens.push(token);
  return tokens;
}

export function formatArguments(tokens: string[]): string {
  return tokens.length ? JSON.stringify(tokens) : '';
}

export function parameterBounds(descriptor?: ParameterDescriptor) {
  const schema = descriptor?.anyOf?.find(item => item.type === 'number' || item.type === 'integer') ?? descriptor;
  return { min: schema?.minimum, max: descriptor?.maximum ?? schema?.maximum, integer: schema?.type === 'integer' };
}

export function shellQuote(value: unknown): string {
  return `'${String(value).replaceAll("'", "'\\''")}'`;
}
