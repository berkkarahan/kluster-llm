import { readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { run } from 'node:test';
import { spec } from 'node:test/reporters';

const files = readdirSync('dashboard/tests')
  .filter((name) => name.endsWith('.test.mjs'))
  .sort()
  .reverse()
  .map((name) => resolve('dashboard/tests', name));
const stream = run({ files, concurrency: 1, isolation: 'process', execArgv: ['--experimental-strip-types'] });
stream.on('test:fail', () => { process.exitCode = 1; });
stream.compose(spec).pipe(process.stdout);
