#!/usr/bin/env python3
"""Build the minimal Apple Device Tree that the SWEET xnu needs to boot.

Only what the early boot path actually reads (see NOTES.md); every MMIO
address comes from the sweet live FDT.  --c21y selects the Realme C21Y
(UMS512) profile built from realme_c21y_boot_dtb.dtb. Usage:
  mkadt.py OUT.adt [--dump] [--qemu] [--highmem] [--c21y]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# Kernel-managed RAM: above every Qualcomm XPU carve-out (see NOTES.md)
PHYS_BASE = 0xb0000000
MEM_SIZE = 0x4d000000
# --highmem (phone only): the whole upper DDR bank. The live FDT memory node is
# <0x80000000 2G>, <0x1_00000000 0xfcc00000>; every fixed reserved-memory
# carve-out is in the low bank and the dynamic pools are limited below 4G.
HIGH_PHYS_BASE = 0x100000000
HIGH_MEM_SIZE = 0xfcc00000
# /arm-io children are relative to this; it must not be 0 (serial_init bails)
SOC_BASE = 0x01000000

GICD, GICD_SIZE = 0x17a00000, 0x10000
GICR, GICR_SIZE = 0x17a60000, 0x100000
FB, FB_SIZE = 0x9c000000, 0x1700000
SWEET_ECID = 0x5357454554000001  # 'SWEET' + 1, synthetic
FB_W, FB_H, FB_STRIDE = 1080, 2400, 1080 * 4
# Internal UFS host (ufshc@1d84000 "ufs_mem"); QEMU virt has none
UFS, UFS_SIZE = 0x01d84000, 0x3000
TIMEBASE = 19200000
VTIMER_INTID = 19
# QEMU virt (-M virt,gic-version=3 -cpu cortex-a76): GICD/GICR and the generic timer's CNTFRQ
QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080a0000, 62500000
QEMU_VTIMER_INTID = 27

# Realme C21Y (Unisoc UMS512), from realme_c21y_boot_dtb.dtb:
#   memory <0x80000000 0x80000000>; every static reserved-memory carve-out
#   ends at 0x94400000, ramoops starts at 0xfff80000.  Keep the kernel below
#   both.  GICD/GICR and the virtual timer PPI 11 -> INTID 27 are from the DT.
C21Y_PHYS_BASE = 0x95000000
C21Y_MEM_SIZE = 0x6a000000
C21Y_SOC_BASE = 0x10000000
C21Y_GICD, C21Y_GICR = 0x12000000, 0x12040000
C21Y_UART, C21Y_UART_SIZE = 0x70100000, 0x100
C21Y_VTIMER_INTID = 27

BOOT_MANIFEST_HASH = hashlib.sha384(b'sweet boot manifest').digest()

# The iOS userland is the iPhone X (iPhone10,3 / D22AP) IPSW. MobileGestalt
# derives product type, device class, home-button-less UI, artwork etc. from
# the root identity and /product of the real D22AP device tree. That tree is
# an Apple artifact (kept out of git): extract it from the IPSW with
#   tools/remotezip.py URL DeviceTree.d22ap.im4p DIR
#   ipsw img4 im4p extract DIR/Firmware/all_flash/DeviceTree.d22ap.im4p -o d22ap.adt
D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt')


def d22ap_product(path=D22AP_ADT):
    """/product properties of the real D22AP tree, without the syscfg
    placeholders iBoot would fill in (flag bit set) and the node bookkeeping."""
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None, qemu=False, highmem=False, c21y=False):
    """qemu=True: same tree, but with QEMU virt's GIC addresses and CNTFRQ (tools/qemu-sweet.py).
    c21y=True: UMS512 addresses and eight heterogeneous cores from the C21Y DT."""
    if qemu and c21y:
        raise SystemExit('--qemu and --c21y are mutually exclusive')
    if c21y:
        phys_base, mem_size = C21Y_PHYS_BASE, C21Y_MEM_SIZE
        gicd, gicr, timebase = C21Y_GICD, C21Y_GICR, None
        soc_base = C21Y_SOC_BASE
    else:
        phys_base, mem_size = (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE)
        gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
        soc_base = SOC_BASE
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('firmware-version', cstr('sweet-shim')),
        ('system-firmware-version', cstr('sweet-shim')),
        # iBoot's SHA-384 of the boot manifest; /sbin/mount -P 1 exits unless
        # it exists (bind mounts /private/preboot/<hex>/usr/standalone/firmware,
        # tolerating a missing directory). Fixed stand-in value.
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        # libimg4 (cryptexd) aborts "failed to get crypto algorithm" without
        # it; real A11+ device trees say sha2-384.
        ('crypto-hash-method', cstr('sha2-384')),
        # MobileGestalt's screen class: display-scale (u32, default 0) comes
        # from /chosen, the pixel size from IOMobileFramebuffer. Scale 0 makes
        # SpringBoard's FBSDisplayMode "infxinf@0x" invalid. 1080x2400 @3x =
        # 360x800 points (like iPhone X class devices). C21Y has no verified
        # framebuffer node yet, so leave this unset there.
        *([] if c21y else [('display-scale', u32(3))]),
        # XNU derives IOPlatformUUID (gethostuuid) from these on arm; without
        # them CFPreferences cannot use kCFPreferencesCurrentHost (no ByHost
        # files), so e.g. DataMigration never records LastSystemVersion and
        # migrates on every boot. Synthetic ECID (not the phone's); chip-id
        # of the A11 that the iPhone X userland expects.
        ('unique-chip-id', struct.pack('<Q', SWEET_ECID)),
        ('chip-id', u32(0x8015)),
        # Security status as on a dev-fused, undemoted board (tuple 0,0,1,1):
        # MobileGestalt reads these from /chosen, and mobileactivationd's
        # DeviceType then takes its native "Hactivation" branch (state
        # Activated without an activation record; no SEP, no baseband here).
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        # Local boot policy (kern_csr.c): SIP relaxed like `csrutil disable`
        # (lp-sip0: untrusted kexts, unrestricted FS, task_for_pid, kernel
        # debugger, dtrace, nvram), lp-sip1: unauthenticated (unsealed) root.
        # The iOS shared cache is in no trust cache, so dyld can only map it
        # with CSR_ALLOW_UNRESTRICTED_FS (vm_unix.c shared region checks).
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # C21Y: six Cortex-A55 cores in cluster 0 and two Cortex-A75 cores in
    # cluster 1 (cpus/cpu@0..500 and cpu@600/700 in the vendor DT).  XNU's
    # cluster-type values are the ASCII 'E' and 'P' used by
    # ml_parse_cpu_topology(); the kernel defaults to one CPU, so use cpus=1
    # for the first boot.  Do not invent timebase-frequency: the vendor DT
    # does not carry it.  Patch 0083 makes XNU read CNTFRQ_EL0 instead.
    cpu_nodes = []
    for core in range(1 if qemu else (8 if c21y else 8)):
        if c21y:
            perf = core >= 6
            cluster = 1 if perf else 0
            core_in_cluster = core - 6 if perf else core
            props = [
                ph(),
                ('device_type', cstr('cpu')),
                ('compatible', cstr('arm,cortex-a75' if perf else 'arm,cortex-a55') + cstr('arm,armv8')),
                ('reg', u32(core << 8)),  # actual MPIDR Aff1:Aff0, NOT logical ID
                ('cpu-id', u32(core)),
                ('cluster-id', u32(cluster)),
                ('cluster-type', u32(ord('P') if perf else ord('E'))),
                ('die-cluster-id', u32(cluster)),
                ('cluster-core-id', u32(core_in_cluster)),
                ('l2-cache-id', u32(core)),
                ('l3-cache-id', u32(0)),
                ('state', cstr('running' if core == 0 else 'stopped')),
            ]
        else:
            props = [
                ph(),
                ('device_type', cstr('cpu')),
                ('compatible', cstr('qcom,kryo470') + cstr('ARM,v8')),
                ('reg', u32(core << 8)),  # actual MPIDR Aff1:Aff0, NOT logical ID
                ('cpu-id', u32(core)),   # descriptive: XNU assigns IDs by DT order
                ('cluster-id', u32(0)),  # descriptive; see ml_parse_cpu_topology
                ('cluster-type', u32(0)),  # homogeneous CLUSTER_TYPE_SMP, not AMP
                ('die-cluster-id', u32(0)),
                ('cluster-core-id', u32(core)),
                ('l2-cache-id', u32(core)),  # distinct per-core L2 nodes in live FDT
                ('l3-cache-id', u32(0)),     # shared L3
                ('state', cstr('running' if core == 0 else 'stopped')),
                ('timebase-frequency', u32(timebase)),
                ('clock-frequency', u32(2304000000 if core >= 6 else 1804800000)),
                ('bus-frequency', u32(100000000)),
                ('memory-frequency', u32(1866000000)),
                ('peripheral-frequency', u32(timebase)),
                ('fixed-frequency', u32(timebase)),
            ]
        cpu_nodes.append(node(f'cpu{core}', props))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(C21Y_VTIMER_INTID if c21y else (QEMU_VTIMER_INTID if qemu else VTIMER_INTID))),
        ('reg', u64(gicd - soc_base, GICD_SIZE, gicr - soc_base, GICR_SIZE)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, soc_base, 0x100000000 - soc_base)),
    ], [gic,
        # No hardware: watchdogd checks IODeviceTree:/arm-io/wdt for no-pmu and
        # otherwise crashes trying to set the PMU boot stage (customer builds
        # ignore the -wdt_no_pmu boot-arg).
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        # Main display: IOMobileFramebuffer.framework counts /arm-io children
        # with this device_type and reads the canvas size from the node
        # (the presented iPhone X size, see SweetFramebuffer.cpp);
        # the kernel's AppleCLCD matches the compatible string.  C21Y has no
        # verified framebuffer address yet, so do not register one.
        *([] if c21y else [node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('sweet,display')),
            ('canvas-width', u32(1125)),
            ('canvas-height', u32(2436)),
        ])])])

    fbcon = None if c21y else node('sweet-fbcon', [
        ph(),
        ('reg', u64(FB, FB_SIZE)),
        ('width', u32(FB_W)),
        ('height', u32(FB_H)),
        ('stride', u32(FB_STRIDE)),
    ])

    # bsd/dev/sweet_ufs.c: the UFS host ABL leaves running (absolute physical reg).
    # C21Y has no UFS node; its boot device is the 71400000.sdio eMMC.
    ufs = None if c21y else node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(UFS, UFS_SIZE)),
    ])

    # C21Y debug UART: serial@70100000, already initialized by ABL's earlycon.
    # The pe_serial.c C21Y patch maps this absolute physical address and
    # registers a polled TX-only console, so XNU output does not depend on a
    # framebuffer that has not been located yet.
    c21y_uart = node('c21y-uart', [
        ph(),
        ('reg', u64(C21Y_UART, C21Y_UART_SIZE)),
    ]) if c21y else None

    # pmap_compute_max_asids requires the node even when no overrides are set.
    # An absent pmap-max-asids property selects XNU's own MAX_ASIDS default.
    # No effaceable storage: keybagd then reads /private/var/keybags/systembag.kb
    # as a plain plist instead of decrypting it with the effaceable key.
    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    # MobileGestalt HomeButtonType = IODeviceTree:/buttons home-button-type
    # (D22AP: 2, no Home button; absent = 0 = Home-button device).
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product()
    product = [node('product', [ph()] + product_props)] if product_props else []

    # Identity of the iPhone X whose userland runs here: compatible[0] is
    # hw.model/hw.target (D22AP), model is hw.machine/hw.product
    # (iPhone10,3). 'xiaomi,sweet' stays for SweetPlatformExpert matching.
    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,sweet') +
         (cstr('sprd,ums512') if c21y else cstr('qcom,sm7150')) + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, buttons] +
       ([c21y_uart] if c21y_uart else []) +
       ([fbcon] if fbcon else []) +
       product +
       ([] if qemu or c21y else [ufs]))


if __name__ == '__main__':
    root = build(qemu='--qemu' in sys.argv, highmem='--highmem' in sys.argv,
                 c21y='--c21y' in sys.argv)
    blob = adt.serialize(root)
    open(sys.argv[1], 'wb').write(blob)
    if '--dump' in sys.argv:
        adt.dump(adt.parse(blob)[0])
    print(f'{sys.argv[1]}: {len(blob)} bytes', file=sys.stderr)
