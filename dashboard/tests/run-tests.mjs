import { spawn } from 'node:child_process';
import { readdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const serial = process.argv.includes('--serial');
const concurrency = serial ? 1 : 4;
const files = (await readdir(root))
  .filter((file) => file.endsWith('.test.mjs'))
  .sort();
const browserFiles = new Set(['models.test.mjs', 'usage-workspace.test.mjs', 'managed-network.test.mjs']);
const parallelFiles = serial ? [] : files.filter((file) => !browserFiles.has(file));
const sequentialFiles = serial ? files : files.filter((file) => browserFiles.has(file));

function run(filesToRun, workers) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [
      '--experimental-strip-types', '--test', `--test-concurrency=${workers}`,
      ...filesToRun.map((file) => join(root, file))
    ], { cwd: join(root, '..'), stdio: 'inherit' });
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`Node test runner exited with ${code ?? signal}`));
    });
  });
}

if (parallelFiles.length) await run(parallelFiles, concurrency);
if (sequentialFiles.length) await run(sequentialFiles, 1);
