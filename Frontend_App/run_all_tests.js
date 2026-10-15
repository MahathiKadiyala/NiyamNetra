// Comprehensive test runner for Frontend_App
const fs = require('fs');
const path = require('path');
const cp = require('child_process');

const testsDir = path.join(__dirname, '__tests__');
const files = fs.readdirSync(testsDir).filter(f => f.endsWith('.js')).sort();

let totalSuitesPassed = 0;
let totalSuitesFailed = 0;
let totalAssertionsPassed = 0;
let totalAssertionsFailed = 0;
const suiteResults = [];

console.log(`=== Running All ${files.length} Frontend Test Suites ===\n`);

files.forEach(f => {
  console.log(`\n========================================`);
  console.log(`Running: ${f}`);
  console.log(`========================================`);
  try {
    const output = cp.execSync(`node "${path.join(testsDir, f)}"`, { encoding: 'utf8' });
    process.stdout.write(output);
    totalSuitesPassed++;

    // Parse assertion counts from output: "Results: X passed, Y failed" or "Summary: X passed, Y failed"
    const match = output.match(/(?:Results:|Summary:)\s+(\d+)\s+passed,\s+(\d+)\s+failed/i);
    if (match) {
      const p = parseInt(match[1], 10);
      const fl = parseInt(match[2], 10);
      totalAssertionsPassed += p;
      totalAssertionsFailed += fl;
      suiteResults.push({ file: f, passed: p, failed: fl, status: 'PASSED' });
    } else {
      suiteResults.push({ file: f, passed: null, failed: null, status: 'PASSED' });
    }
  } catch (err) {
    if (err.stdout) process.stdout.write(err.stdout);
    if (err.stderr) process.stderr.write(err.stderr);
    console.error(`FAILED: ${f}`);
    totalSuitesFailed++;

    const match = (err.stdout || '').match(/(?:Results:|Summary:)\s+(\d+)\s+passed,\s+(\d+)\s+failed/i);
    if (match) {
      const p = parseInt(match[1], 10);
      const fl = parseInt(match[2], 10);
      totalAssertionsPassed += p;
      totalAssertionsFailed += fl;
      suiteResults.push({ file: f, passed: p, failed: fl, status: 'FAILED' });
    } else {
      suiteResults.push({ file: f, passed: null, failed: null, status: 'FAILED' });
    }
  }
});

console.log(`\n========================================`);
console.log(`Frontend Test Suite Breakdown:`);
suiteResults.forEach(r => {
  const assertStr = r.passed !== null ? `(${r.passed} passed, ${r.failed} failed)` : '';
  console.log(`  - ${r.file}: ${r.status} ${assertStr}`);
});
console.log(`----------------------------------------`);
console.log(`Suites Summary: ${totalSuitesPassed} passed, ${totalSuitesFailed} failed, 0 skipped (total ${files.length} suites)`);
console.log(`Assertions Summary: ${totalAssertionsPassed} passed, ${totalAssertionsFailed} failed, 0 skipped (total ${totalAssertionsPassed + totalAssertionsFailed} assertions)`);
console.log(`========================================\n`);

if (totalSuitesFailed > 0 || totalAssertionsFailed > 0) {
  process.exit(1);
} else {
  process.exit(0);
}
