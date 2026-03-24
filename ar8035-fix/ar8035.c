/*
 * ar8035.c 
 * No libc, raw syscalls only, no FPU
 * MIPS big-endian, O32 ABI
 */

/* MIPS Linux syscall numbers */
#define __NR_write   4004
#define __NR_socket  4183
#define __NR_ioctl   4054
#define __NR_exit    4001

/* ioctl numbers */
#define SIOCGMIIPHY  0x8947
#define SIOCGMIIREG  0x8948
#define SIOCSMIIREG  0x8949
#define SIOCGIFHWADDR 0x8927
#define AF_INET      2
#define SOCK_DGRAM   2

struct mii_data {
    unsigned short phy_id;
    unsigned short reg_num;
    unsigned short val_in;
    unsigned short val_out;
};

struct ifreq {
    char ifr_name[16];
    union {
        struct mii_data mii;
        char pad[24];
    } u;
};

/*
 * MIPS O32 ABI: on error, $a3=1 and $v0=errno (positive).
 * On success, $a3=0 and $v0=return value.
 * Previous version only checked (v0 < 0), missing positive errno.
 *
 * IMPORTANT: The MIPS Linux O32 kernel syscall ABI does NOT guarantee
 * $t0-$t9 ($8-$15, $24-$25) are preserved across syscalls. glibc knows
 * this -- we must declare them as clobbers so GCC does not keep live values
 * in those registers across the syscall instruction.
 */
#define SYSCALL_CLOBBERS \
    "v1", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9", \
    "memory"

static long sys_write(int fd, const void *buf, long len)
{
    register long r   __asm__("v0") = __NR_write;
    register long err __asm__("a3");
    register long a0  __asm__("a0") = fd;
    register long a1  __asm__("a1") = (long)buf;
    register long a2  __asm__("a2") = len;
    __asm__ volatile("syscall" : "+r"(r), "=r"(err) : "r"(a0), "r"(a1), "r"(a2) : SYSCALL_CLOBBERS);
    return err ? -r : r;
}

static long sys_socket(int domain, int type, int proto)
{
    register long r   __asm__("v0") = __NR_socket;
    register long err __asm__("a3");
    register long a0  __asm__("a0") = domain;
    register long a1  __asm__("a1") = type;
    register long a2  __asm__("a2") = proto;
    __asm__ volatile("syscall" : "+r"(r), "=r"(err) : "r"(a0), "r"(a1), "r"(a2) : SYSCALL_CLOBBERS);
    return err ? -r : r;
}

static long sys_ioctl(int fd, long req, void *arg)
{
    register long r   __asm__("v0") = __NR_ioctl;
    register long err __asm__("a3");
    register long a0  __asm__("a0") = fd;
    register long a1  __asm__("a1") = req;
    register long a2  __asm__("a2") = (long)arg;
    __asm__ volatile("syscall" : "+r"(r), "=r"(err) : "r"(a0), "r"(a1), "r"(a2) : SYSCALL_CLOBBERS);
    return err ? -r : r;
}

static void sys_exit(int code)
{
    register long r  __asm__("v0") = __NR_exit;
    register long a0 __asm__("a0") = code;
    __asm__ volatile("syscall" : : "r"(r), "r"(a0) : "memory");
    __builtin_unreachable();
}

/* Minimal string/mem helpers */
static void memset_z(void *p, int n)
{
    char *c = (char *)p;
    while (n--) *c++ = 0;
}

static void str_copy(char *dst, const char *src, int max)
{
    int i = 0;
    while (i < max - 1 && src[i]) { dst[i] = src[i]; i++; }
    dst[i] = 0;
}

/* Print helpers */
static void print(const char *s)
{
    const char *p = s;
    while (*p) p++;
    sys_write(1, s, p - s);
}

static void println(const char *s) { print(s); print("\n"); }

static char nibble_to_hex(unsigned n)
{
    n &= 0xF;
    return n < 10 ? '0' + n : 'a' + n - 10;
}

static void print_hex16(unsigned val)
{
    char buf[7] = "0x";
    buf[2] = nibble_to_hex(val >> 12);
    buf[3] = nibble_to_hex(val >> 8);
    buf[4] = nibble_to_hex(val >> 4);
    buf[5] = nibble_to_hex(val);
    buf[6] = '\0';
    print(buf);
}

/* Print signed decimal -- only handles small values */
static void print_dec(long v)
{
    char buf[16];
    int i = 15;
    buf[i] = '\0';
    if (v == 0) { print("0"); return; }
    int neg = v < 0;
    if (neg) v = -v;
    while (v > 0 && i > 0) {
        buf[--i] = '0' + (v % 10);
        v /= 10;
    }
    if (neg) buf[--i] = '-';
    print(&buf[i]);
}

/* MDIO state */
static int sock;
static char ifname[16];
static int phy_addr;

static long mdio_ioctl(long req, struct ifreq *ifr)
{
    long ret = sys_ioctl(sock, req, ifr);
    return ret;
}

static int mdio_read(int reg)
{
    struct ifreq ifr;
    long ret;
    memset_z(&ifr, sizeof(ifr));
    str_copy(ifr.ifr_name, ifname, 16);
    ifr.u.mii.phy_id  = phy_addr;
    ifr.u.mii.reg_num = reg;
    ret = mdio_ioctl(SIOCGMIIREG, &ifr);
    if (ret < 0) {
        print("  [ERROR] SIOCGMIIREG reg="); print_hex16(reg);
        print(" errno="); print_dec(-ret); print("\n");
        return -1;
    }
    return ifr.u.mii.val_out;
}

static int mdio_write(int reg, int val)
{
    struct ifreq ifr;
    long ret;
    memset_z(&ifr, sizeof(ifr));
    str_copy(ifr.ifr_name, ifname, 16);
    ifr.u.mii.phy_id  = phy_addr;
    ifr.u.mii.reg_num = reg;
    ifr.u.mii.val_in  = (unsigned short)val;
    ret = mdio_ioctl(SIOCSMIIREG, &ifr);
    if (ret < 0) {
        print("  [ERROR] SIOCSMIIREG reg="); print_hex16(reg);
        print(" errno="); print_dec(-ret); print("\n");
        return -1;
    }
    return 0;
}

static int dbg_read(int dbg_reg)
{
    if (mdio_write(0x1D, dbg_reg) < 0) return -1;
    return mdio_read(0x1E);
}

static int dbg_write(int dbg_reg, int val)
{
    if (mdio_write(0x1D, dbg_reg) < 0) return -1;
    return mdio_write(0x1E, val);
}

static int dbg_mask(int dbg_reg, int clear_bits, int set_bits)
{
    int val = dbg_read(dbg_reg);
    if (val < 0) return val;
    val = (val & ~clear_bits) | set_bits;
    return dbg_write(dbg_reg, val);
}

int ar8035_main(void)
{
    str_copy(ifname, "eth0", 16);
    phy_addr = 3;

    sock = (int)sys_socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        print("[ERROR] socket() errno="); print_dec(-sock); print("\n");
        sys_exit(1);
    }
    print("[*] sock fd="); print_dec(sock); print("\n");

    /* Diagnostic: try SIOCGIFHWADDR to verify basic ioctl works */
    {
        struct ifreq ifr;
        long ret;
        memset_z(&ifr, sizeof(ifr));
        str_copy(ifr.ifr_name, "eth0", 16);
        ret = sys_ioctl(sock, SIOCGIFHWADDR, &ifr);
        if (ret < 0) {
            print("[!] SIOCGIFHWADDR failed errno="); print_dec(-ret); print("\n");
        } else {
            /* MAC is in ifr.u.pad[2..7] (sa_data bytes 0-5 at offset +2) */
            unsigned char *mac = (unsigned char *)&ifr.u.pad[2];
            print("[*] eth0 MAC: ");
            print_hex16(mac[0]); print(":");
            print_hex16(mac[1]); print(":");
            print_hex16(mac[2]); print(":");
            print_hex16(mac[3]); print(":");
            print_hex16(mac[4]); print(":");
            print_hex16(mac[5]); print("\n");
        }
    }

    /* SIOCGMIIPHY -- query which PHY the driver uses */
    {
        struct ifreq ifr;
        long ret;
        memset_z(&ifr, sizeof(ifr));
        str_copy(ifr.ifr_name, "eth0", 16);
        ret = sys_ioctl(sock, SIOCGMIIPHY, &ifr);
        if (ret < 0) {
            print("[!] SIOCGMIIPHY failed errno="); print_dec(-ret); print("\n");
        } else {
            print("[*] SIOCGMIIPHY phy_id="); print_hex16(ifr.u.mii.phy_id); print("\n");
            /* Use the driver-reported PHY address */
            phy_addr = ifr.u.mii.phy_id;
        }
    }

    print("[*] AR8035 fix: eth0 phy_addr="); print_dec(phy_addr); print("\n");

    /* PHY ID check */
    int id1 = mdio_read(0x02);
    int id2 = mdio_read(0x03);
    print("[*] PHY ID: "); print_hex16(id1); print(" "); print_hex16(id2);
    println(" (AR8035 = 0x004d 0xd072)");

    if ((id1 & 0xFFFF) != 0x004d) {
        println("[!] PHY ID mismatch -- MDIO may not be working");
        println("[!] Check above errors; aborting register writes");
        sys_exit(1);
    }

    /* Step 1: Disable Hibernation (debug reg 0x0B, bit 15 = PS_HIB_EN) */
    println("[*] Disabling hibernation (debug 0x0B bit 15)...");
    int val = dbg_read(0x0B);
    print("  debug[0x0B] before: "); print_hex16(val); println("");
    dbg_mask(0x0B, 0x8000, 0);
    val = dbg_read(0x0B);
    print("  debug[0x0B] after:  "); print_hex16(val); println("");

    /* Step 2: Enable RGMII RX clock delay (debug reg 0x00, bit 15) */
    println("[*] Enabling RGMII RX clock delay (debug 0x00 bit 15)...");
    val = dbg_read(0x00);
    print("  debug[0x00] before: "); print_hex16(val); println("");
    dbg_mask(0x00, 0, 0x8000);
    val = dbg_read(0x00);
    print("  debug[0x00] after:  "); print_hex16(val); println("");

    /* BMSR status */
    val = mdio_read(0x01);
    print("[*] BMSR (0x01): "); print_hex16(val); println("");

    println("[+] Done.");
    return 0;
}
