import sys, plistlib
p, m3u = sys.argv[1], sys.argv[2]
with open(p,'rb') as f: d=plistlib.load(f)
d['ProgramArguments']=['/Users/Ethan/bin/ncplay-runner', m3u]
with open(p,'wb') as f: plistlib.dump(d,f)
print('plist updated')