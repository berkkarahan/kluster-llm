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

export type PrimaSwaMode = 'compact' | 'full';
export type PrimaHeadMode = 'generation' | 'embedding' | 'reranking';
export type PrimaPoolingMode = '' | 'none' | 'mean' | 'cls' | 'last' | 'rank';
export type PrimaLoraAdapter = { path: string; scale: string };

export type PrimaExtrasState = {
  swaMode: PrimaSwaMode;
  headMode: PrimaHeadMode;
  pooling: PrimaPoolingMode;
  loraAdapters: PrimaLoraAdapter[];
  templateKwargsText: string;
  otherTokens: string[];
  parseError: string;
};

const VALID_POOLING = new Set<PrimaPoolingMode>(['', 'none', 'mean', 'cls', 'last', 'rank']);

export type PrimaExtrasPatch = Partial<Omit<PrimaExtrasState, 'otherTokens' | 'parseError'>>;

type PrimaExtrasGroup = keyof PrimaExtrasPatch;
type PrimaExtrasSpan = { group: PrimaExtrasGroup; start: number; end: number; error: string };

function primaExtrasGroup(flag: string): PrimaExtrasGroup | undefined {
  switch (flag) {
    case '--swa-full': case '--swa-compact': return 'swaMode';
    case '--embedding': case '--embeddings': case '--rerank': case '--reranking': return 'headMode';
    case '--pooling': return 'pooling';
    case '--lora': case '--lora-scaled': return 'loraAdapters';
    case '--chat-template-kwargs': return 'templateKwargsText';
  }
}

function isTemplateKwargs(text: string | null): boolean {
  try {
    const value: unknown = JSON.parse(text ?? '');
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  } catch { return false; }
}

function isLoraScale(text: string | undefined): boolean {
  return typeof text === 'string' && /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(text.trim()) && Number.isFinite(Number(text));
}

function loraArguments(adapter: PrimaLoraAdapter): string[] {
  if (!adapter || typeof adapter.path !== 'string' || !adapter.path.length) {
    throw extrasEditError('loraAdapters', 'each adapter requires a nonempty literal path');
  }
  if (!isLoraScale(adapter.scale)) throw extrasEditError('loraAdapters', 'each scale must be a finite number');
  const flag = Number(adapter.scale) === 1 ? '--lora' : '--lora-scaled';
  const result = adapter.path.startsWith('-') ? [`${flag}=${adapter.path}`] : [flag, adapter.path];
  if (flag === '--lora-scaled') result.push(adapter.scale);
  return result;
}

function extrasEditError(group: string, reason: string): Error {
  return new Error(`Cannot edit ${group}: ${reason}. Repair Extra arguments in the raw editor first.`);
}

/** Edit only requested groups; unknown arguments remain opaque, not validated as safe. */
export function patchPrimaExtras(original: string, patch: PrimaExtrasPatch): string {
  if (!patch || typeof patch !== 'object' || ![Object.prototype, null].includes(Object.getPrototypeOf(patch))) {
    throw extrasEditError('patch', 'expected a plain object of requested fields');
  }
  const allowed = ['swaMode', 'headMode', 'pooling', 'loraAdapters', 'templateKwargsText'];
  for (const key of Reflect.ownKeys(patch)) {
    if (typeof key !== 'string' || !allowed.includes(key)) throw extrasEditError('patch', `unknown field ${String(key)}`);
  }
  const groups = Object.keys(patch) as PrimaExtrasGroup[];
  if (!groups.length) return original;
  for (const group of groups) {
    const value = patch[group];
    if ((group === 'swaMode' && value !== 'compact' && value !== 'full') ||
        (group === 'headMode' && value !== 'generation' && value !== 'embedding' && value !== 'reranking') ||
        (group === 'pooling' && !VALID_POOLING.has(value as PrimaPoolingMode)) ||
        (group === 'loraAdapters' && !Array.isArray(value)) ||
        (group === 'templateKwargsText' && typeof value !== 'string')) {
      throw extrasEditError(group, 'invalid patch value');
    }
  }
  let tokens: string[];
  try { tokens = parseArgumentText(original); }
  catch { throw extrasEditError('patch', 'expected a JSON array of strings or correctly quoted argument text'); }
  let insertion = tokens.length;
  const spans: PrimaExtrasSpan[] = [];
  for (let i = 0; i < tokens.length; i++) {
    if (tokens[i] === '--') { insertion = i; break; }
    const start = i;
    const eq = tokens[i].indexOf('=');
    const flag = eq < 0 ? tokens[i] : tokens[i].slice(0, eq);
    const inline = eq < 0 ? null : tokens[i].slice(eq + 1);
    const group = primaExtrasGroup(flag);
    if (!group) continue;
    let error = '';
    if (group === 'swaMode' || group === 'headMode') {
      if (inline !== null) error = `${flag} does not take a value`;
    } else {
      let value = inline;
      if (value === null && i + 1 < tokens.length && !tokens[i + 1].startsWith('-')) value = tokens[++i];
      if (group === 'pooling' && (!value || !VALID_POOLING.has(value as PrimaPoolingMode))) {
        error = `${flag} requires a valid pooling value`;
      }
      if (group === 'templateKwargsText' && !isTemplateKwargs(value)) error = `${flag} requires a JSON object`;
      if (group === 'loraAdapters') {
        if (value === null && tokens[i + 1]?.startsWith('-')) {
          // A dash-prefixed path could also be a requested flag: neither interpretation is safe.
          const nextGroup = primaExtrasGroup(tokens[i + 1].split('=')[0]);
          if (nextGroup && groups.includes(nextGroup)) throw extrasEditError(nextGroup, `ambiguous path after ${flag}; use ${flag}=PATH`);
        }
        if (!value) error = `${flag} requires a nonempty literal path`;
        if (flag === '--lora-scaled' && value !== null) {
          const scale = tokens[i + 1];
          if (!isLoraScale(scale)) error = `${flag} requires a finite scale after its path`;
          // Negative numbers are values, never treat a following option as the scale.
          if (scale !== undefined && (!scale.startsWith('-') || isLoraScale(scale))) i++;
        }
      }
    }
    spans.push({ group, start, end: i + 1, error });
  }

  const edits: { start: number; end: number; tokens: string[] }[] = [];
  for (const group of groups) {
    const owned = spans.filter(span => span.group === group);
    if (group !== 'loraAdapters' && owned.length > 1) throw extrasEditError(group, 'duplicate or conflicting flags');
    const malformed = owned.find(span => span.error);
    if (malformed) throw extrasEditError(group, malformed.error);
    if (group === 'loraAdapters') {
      const adapters = patch.loraAdapters!.map(loraArguments);
      const interleaved = owned.some((span, index) => index > 0 && owned[index - 1].end !== span.start);
      if (interleaved && adapters.length && adapters.length !== owned.length) {
        throw extrasEditError(group, 'changing adapter count across interleaved arguments is ambiguous');
      }
      if (adapters.length === owned.length || !adapters.length) {
        owned.forEach((span, index) => edits.push({ ...span, tokens: adapters[index] ?? [] }));
      } else {
        edits.push({ start: owned[0]?.start ?? insertion, end: owned.at(-1)?.end ?? insertion, tokens: adapters.flat() });
      }
      continue;
    }
    let replacement: string[] = [];
    if (group === 'swaMode') replacement = [`--swa-${patch.swaMode}`];
    if (group === 'headMode' && patch.headMode !== 'generation') {
      replacement = [patch.headMode === 'embedding' ? '--embedding' : '--rerank'];
    }
    if (group === 'pooling' && patch.pooling) replacement = ['--pooling', patch.pooling];
    if (group === 'templateKwargsText' && patch.templateKwargsText !== '') {
      if (!isTemplateKwargs(patch.templateKwargsText!)) throw extrasEditError(group, 'template kwargs must be a JSON object');
      replacement = ['--chat-template-kwargs', patch.templateKwargsText!];
    }
    const span = owned[0];
    edits.push({ start: span?.start ?? insertion, end: span?.end ?? insertion, tokens: replacement });
  }
  // Apply from the end so every edit refers to the original token positions.
  for (const edit of edits.reverse().sort((a, b) => b.start - a.start)) {
    tokens.splice(edit.start, edit.end - edit.start, ...edit.tokens);
  }
  return formatArguments(tokens);
}

export function parsePrimaExtras(extraArgsText: string): PrimaExtrasState {
  const state: PrimaExtrasState = {
    swaMode: 'compact',
    headMode: 'generation',
    pooling: '',
    loraAdapters: [],
    templateKwargsText: '',
    otherTokens: [],
    parseError: '',
  };
  let tokens: string[] = [];
  try {
    tokens = parseArgumentText(extraArgsText);
  } catch (err) {
    state.parseError = err instanceof Error ? err.message : String(err);
    return state;
  }

  for (let i = 0; i < tokens.length; i++) {
    const raw = tokens[i];
    const eqIndex = raw.indexOf('=');
    const flag = eqIndex >= 0 ? raw.slice(0, eqIndex) : raw;
    const inlineVal = eqIndex >= 0 ? raw.slice(eqIndex + 1) : null;

    if (flag === '--swa-full') {
      state.swaMode = 'full';
    } else if (flag === '--swa-compact') {
      state.swaMode = 'compact';
    } else if (flag === '--embedding' || flag === '--embeddings') {
      state.headMode = 'embedding';
    } else if (flag === '--reranking' || flag === '--rerank') {
      state.headMode = 'reranking';
    } else if (flag === '--pooling') {
      const next = inlineVal ?? tokens[i + 1] ?? '';
      if (inlineVal === null && i + 1 < tokens.length) i++;
      state.pooling = VALID_POOLING.has(next as PrimaPoolingMode) ? (next as PrimaPoolingMode) : '';
    } else if (flag === '--lora') {
      const path = inlineVal ?? tokens[i + 1] ?? '';
      if (inlineVal === null && i + 1 < tokens.length) i++;
      if (path) state.loraAdapters.push({ path, scale: '1' });
    } else if (flag === '--lora-scaled') {
      if (inlineVal !== null) {
        const nextScale = tokens[i + 1] ?? '1';
        if (i + 1 < tokens.length) i++;
        state.loraAdapters.push({ path: inlineVal, scale: nextScale });
      } else {
        const path = tokens[i + 1] ?? '';
        const scale = tokens[i + 2] ?? '1';
        if (i + 2 < tokens.length) i += 2;
        else if (i + 1 < tokens.length) i += 1;
        if (path) state.loraAdapters.push({ path, scale });
      }
    } else if (flag === '--chat-template-kwargs') {
      const jsonText = inlineVal ?? tokens[i + 1] ?? '';
      if (inlineVal === null && i + 1 < tokens.length) i++;
      state.templateKwargsText = jsonText;
    } else {
      state.otherTokens.push(raw);
    }
  }
  return state;
}

export function serializePrimaExtras(state: Omit<PrimaExtrasState, 'parseError'>): string {
  const tokens: string[] = [];
  if (state.swaMode === 'full') {
    tokens.push('--swa-full');
  }
  if (state.headMode === 'embedding') {
    tokens.push('--embedding');
    if (state.pooling) tokens.push('--pooling', state.pooling);
  } else if (state.headMode === 'reranking') {
    tokens.push('--rerank');
    if (state.pooling && state.pooling !== 'rank') {
      tokens.push('--pooling', state.pooling);
    }
  } else if (state.pooling) {
    tokens.push('--pooling', state.pooling);
  }
  for (const adapter of state.loraAdapters) {
    const path = adapter.path.trim();
    if (!path) continue;
    const scale = adapter.scale.trim();
    const numericScale = Number(scale);
    if (!scale || (Number.isFinite(numericScale) && numericScale === 1)) {
      tokens.push('--lora', path);
    } else {
      tokens.push('--lora-scaled', path, scale);
    }
  }
  const kwargs = state.templateKwargsText.trim();
  if (kwargs) {
    tokens.push('--chat-template-kwargs', kwargs);
  }
  tokens.push(...state.otherTokens);
  return formatArguments(tokens);
}
