import { existsSync, rmSync } from 'node:fs';

/** Remove a Chrome profile only after browser-owned file handles have been released. */
export async function removeBrowserProfile(path) {
  let lastError;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      rmSync(path, { recursive: true, force: true });
      if (!existsSync(path)) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw lastError ?? new Error(`Chrome profile remains locked: ${path}`);
}
