import os, glob, openwakeword
pkg = os.path.dirname(openwakeword.__file__)
print("walking", pkg)
for root, dirs, files in os.walk(pkg):
    for f in files:
        if f.endswith(".onnx") or f.endswith(".tflite"):
            print(os.path.join(root, f).replace(pkg, ""))
# also check user data dir
for cand in [os.path.expanduser("~/.openwakeword"), os.path.expanduser("~/openwakeword")]:
    print("user dir", cand, os.path.isdir(cand))
