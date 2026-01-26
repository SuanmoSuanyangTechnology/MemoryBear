import ctypes
import os
import sys
import traceback
from base64 import b64decode


# Setup exception hook
def excepthook(etype, value, tb):
    sys.stderr.write("".join(traceback.format_exception(etype, value, tb)))
    sys.stderr.flush()
    sys.exit(-1)


sys.excepthook = excepthook

# Load security library if available
lib = ctypes.CDLL("./libpython.so")
lib.init_seccomp.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool]
lib.init_seccomp.restype = None

# Get running path
running_path = sys.argv[1]
if not running_path:
    exit(-1)

# Get decrypt key
key = sys.argv[2]
if not key:
    exit(-1)

key = b64decode(key)

os.chdir(running_path)

# Preload code
{{preload}}

# Apply security if library is available
lib.init_seccomp({{uid}}, {{gid}}, {{enable_network}})

# Decrypt and execute code
code = b64decode("{{code}}")


def decrypt(code, key):
    key_len = len(key)
    code_len = len(code)
    code = bytearray(code)
    for i in range(code_len):
        code[i] = code[i] ^ key[i % key_len]
    return bytes(code)


code = decrypt(code, key)
exec(code)
