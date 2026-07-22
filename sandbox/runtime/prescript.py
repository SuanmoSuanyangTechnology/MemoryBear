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
lib.init_seccomp.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint64, ctypes.c_bool]
lib.init_seccomp.restype = ctypes.c_int

lib.apply_landlock.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
lib.apply_landlock.restype = ctypes.c_int

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

# Drop empty / relative entries from sys.path before applying the sandbox.
# Resolving an empty "" entry (cwd) during import calls getcwd(), which is not
# in the seccomp allowlist and would kill the process with SIGSYS. The remaining
# absolute entries (stdlib, etc.) resolve fine after chroot, so we keep only
# non-empty absolute paths.
sys.path = [p for p in sys.path if p]

# Apply security if library is available
privilege = {{privilege}}
if not privilege:
    # Non-privileged mode: apply Landlock for all allowed paths before seccomp
    import json
    allowed = json.loads("""{{allowed_paths}}""")
    arr = (ctypes.c_char_p * (len(allowed) + 1))()
    for i, p in enumerate(allowed):
        arr[i] = p.encode()
    arr[-1] = None
    rc = lib.apply_landlock(arr)
    if rc != 0:
        raise Exception(f"Landlock failed - {str(rc)}")
init_status = lib.init_seccomp({{uid}}, {{gid}}, {{enable_network}}, {{max_as}}, privilege)
if init_status != 0:
    raise Exception(f"code executor err - {str(init_status)}")
del lib

# Preload code
{{preload}}
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
