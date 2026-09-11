const fs = require('fs');
const path = require('path');

const srcDir = path.join(__dirname, 'aira', 'static');
const targets = ['public', 'dist', 'build', 'out'];

targets.forEach(target => {
  const targetDir = path.join(__dirname, target);
  fs.mkdirSync(targetDir, { recursive: true });

  // Copy index.html
  const indexPath = path.join(srcDir, 'index.html');
  if (fs.existsSync(indexPath)) {
    fs.copyFileSync(indexPath, path.join(targetDir, 'index.html'));
    fs.copyFileSync(indexPath, path.join(targetDir, '404.html'));
  }

  // Copy favicon.svg
  const favPath = path.join(srcDir, 'favicon.svg');
  if (fs.existsSync(favPath)) {
    fs.copyFileSync(favPath, path.join(targetDir, 'favicon.svg'));
  }

  // Write _redirects for SPA routing
  fs.writeFileSync(path.join(targetDir, '_redirects'), '/*    /index.html   200\n');

  // Write _headers
  fs.writeFileSync(
    path.join(targetDir, '_headers'),
    '/*\n  X-Frame-Options: SAMEORIGIN\n  X-Content-Type-Options: nosniff\n'
  );

  console.log(`[OK] Built deployment output in: ./${target}`);
});

console.log('Static site build finished successfully.');
