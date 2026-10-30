import './build.mjs';
import { spawn } from 'node:child_process';

const child = spawn(process.execPath, ['--test', 'tests/js/sdk-bridge.test.mjs'], {
  stdio: 'inherit', env: { ...process.env, WAYOOM_LOOPBACK_FAKE: '1' },
});
child.on('exit', code => { process.exitCode = code ?? 1; });
child.on('error', () => { process.exitCode = 1; });
