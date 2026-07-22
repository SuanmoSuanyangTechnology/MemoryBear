let argv = process.argv

let koffi = require('koffi')

process.chdir(argv[2])

let lib = koffi.load("./libnodejs.so")
/** @type {(uid: number, gid: number, enableNetwork: boolean, maxAs: number, privilege: boolean) => number} */
let initSeccomp = lib.func('int init_seccomp(int, int, bool, uint64_t, bool)')
/** @type {(path: string) => number} */
let applyLandlock = lib.func('int apply_landlock_one(char*)')

let uid = parseInt(argv[3])
let gid = parseInt(argv[4])

let options = JSON.parse(argv[5])

let privilege = options['privilege'] !== false
if (!privilege) {
    let ll_rc = applyLandlock(argv[2])
    if (ll_rc !== 0) {
        throw `Landlock failed - ${ll_rc}`
    }
}
// Force initialization of stdout/stderr streams before seccomp blocks ioctl.
// Node.js lazily creates these SyncWriteStream wrappers on first access; the
// internal setup calls ioctl(fd, TCGETS) to check TTY status.  Doing it now
// ensures user code can write to stdout/stderr after seccomp is applied.
void process.stdout.isTTY
void process.stderr.isTTY

let seccomp_init = initSeccomp(uid, gid, options['enable_network'], options['max_as'], privilege)
if (seccomp_init !== 0) {
    throw `code executor err - ${seccomp_init}`
}

delete process.argv
argv = undefined
koffi = undefined
lib = undefined
initSeccomp = undefined
applyLandlock = undefined
uid = undefined
gid = undefined
options = undefined
seccomp_init = undefined

{{code}}
