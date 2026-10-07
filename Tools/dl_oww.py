from openwakeword.utils import download_models
import os
# Where does it store?
try:
    from openwakeword import utils
    print("utils file:", utils.__file__)
except Exception as e:
    print("err", e)
try:
    download_models()
    print("download_models() ok")
except Exception as e:
    print("download err:", repr(e))
