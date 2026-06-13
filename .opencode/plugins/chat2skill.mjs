// Chat2Skill OpenCode plugin.
//
// Adds relevant learned skills to the system prompt by calling the portable
// Chat2Skill retrieval CLI. The CLI is the source of truth shared by all
// adapters in this repo.

import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const pluginDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(pluginDir, '..', '..');
const retrieveScript = path.join(repoRoot, 'scripts', 'retrieve_for_prompt.py');

function taskText(input) {
  if (!input) return '';
  if (typeof input === 'string') return input;
  for (const key of ['prompt', 'message', 'input', 'text']) {
    if (typeof input[key] === 'string') return input[key];
  }
  return JSON.stringify(input).slice(0, 4000);
}

function retrieve(input) {
  const task = taskText(input);
  if (!task.trim()) return '';
  const result = spawnSync('python3', [retrieveScript, task], {
    cwd: repoRoot,
    encoding: 'utf8',
    timeout: 10000,
  });
  if (result.status !== 0) return '';
  const output = (result.stdout || '').trim();
  if (!output || output.includes('No relevant Chat2Skill skills found.')) return '';
  return output;
}

export default async () => ({
  'experimental.chat.system.transform': async (input, output) => {
    const snippet = retrieve(input);
    if (snippet) {
      output.system.push(snippet);
    }
  },
});
