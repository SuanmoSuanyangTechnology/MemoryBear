let argv = process.argv

let koffi = require('koffi')

process.chdir(argv[2])

let lib = koffi.load("./libnodejs.so")
/** @type {(uid: number, gid: number, enableNetwork: boolean) => number} */
let initSeccomp = lib.func('int init_seccomp(int, int, bool)')

let uid = parseInt(argv[3])
let gid = parseInt(argv[4])

let options = JSON.parse(argv[5])

let seccomp_init = initSeccomp(uid, gid, options['enable_network'])
if (seccomp_init !== 0) {
    throw `code executor err - ${seccomp_init}`
}

delete process.argv
argv = undefined
koffi = undefined
lib = undefined
initSeccomp = undefined
uid = undefined
gid = undefined
options = undefined
seccomp_init = undefined

{{code}}
