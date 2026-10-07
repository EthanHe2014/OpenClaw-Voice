
import shutil
p = "/Users/Ethan/.openclaw/workspace/spark/.venv/lib/python3.14/site-packages/openwakeword/train.py"
shutil.copy(p, p + ".bak2")
src = open(p).read()
old = '''        X_val_fp = np.load(config["false_positive_validation_data_path"])
        X_val_fp = np.array([X_val_fp[i:i+input_shape[0]] for i in range(0, X_val_fp.shape[0]-input_shape[0], 1)])  # reshape to match model
        X_val_fp_labels = np.zeros(X_val_fp.shape[0]).astype(np.float32)
'''
new = '''        _fp_path = config["false_positive_validation_data_path"]
        import os as _os_fp
        if not _fp_path or not _os_fp.path.exists(_fp_path):
            _fp_path = _os_fp.path.join(feature_save_dir, "negative_features_test.npy")
            print("WARNING: false_positive_validation_data_path empty/missing; falling back to", _fp_path)
        _X_val_fp_raw = np.load(_fp_path)
        X_val_fp = np.array([_X_val_fp_raw[i:i+input_shape[0]] for i in range(0, _X_val_fp_raw.shape[0]-input_shape[0], 1)])  # reshape to match model
        X_val_fp_labels = np.zeros(X_val_fp.shape[0]).astype(np.float32)
'''
assert old in src, "anchor not found"
src = src.replace(old, new, 1)
open(p, "w").write(src)
print("patched ok")
