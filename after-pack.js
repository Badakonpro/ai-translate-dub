const path = require('path');
const plist = require('plist');
const fs = require('fs');

exports.default = async function(context) {
  const appOutDir = context.appOutDir;
  const appName = context.packager.appInfo.productName;
  const appPath = path.join(appOutDir, `${appName}.app`);
  const plistPath = path.join(appPath, 'Contents', 'Info.plist');

  if (!fs.existsSync(plistPath)) return;

  const raw = fs.readFileSync(plistPath, 'utf8');
  const data = plist.parse(raw);

  let changed = false;
  if (data.ElectronAsarIntegrity) {
    delete data.ElectronAsarIntegrity;
    changed = true;
    console.log('  afterPack: removed ElectronAsarIntegrity');
  }
  if (data.NSMainNibFile) {
    delete data.NSMainNibFile;
    changed = true;
    console.log('  afterPack: removed NSMainNibFile');
  }

  if (changed) {
    fs.writeFileSync(plistPath, plist.build(data));
  }
};
