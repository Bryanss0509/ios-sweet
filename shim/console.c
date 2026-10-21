/*
 * Minimal text console on the ABL splash framebuffer. With the MMU off
 * every access is Device memory, so stay aligned (-mstrict-align).
 */
#include "console.h"
#include "font.h"

#if defined(C21Y)
/*
 * Realme C21Y debug UART, serial@70100000.  ABL has already configured it
 * for earlycon at 115200n8; use the Spreadtrum polling path from
 * drivers/tty/serial/sprd_serial.c: TXD at 0x00, STS1 at 0x0c and the TX
 * FIFO count in bits 15:8.  A bounded wait keeps a dead UART from hanging.
 */
#define UART_BASE		0x70100000UL
#define UART_TXD		0x00
#define UART_STS1		0x0c
#define UART_TX_FIFO_CNT_MASK	0x0000ff00U

volatile uint32_t *con_fb;

static inline uint32_t rd32(uintptr_t a)
{
	return *(volatile uint32_t *)a;
}

static inline void wr32(uintptr_t a, uint32_t v)
{
	*(volatile uint32_t *)a = v;
}

static void uart_putc(char c)
{
	for (volatile int i = 0; i < 1000000; i++) {
		if ((rd32(UART_BASE + UART_STS1) & UART_TX_FIFO_CNT_MASK) == 0)
			break;
	}
	wr32(UART_BASE + UART_TXD, (uint8_t)c);
}

static void uart_puts(const char *s)
{
	while (*s) {
		if (*s == '\n')
			uart_putc('\r');
		uart_putc(*s++);
	}
}

void con_clear(uint32_t bg)
{
	(void)bg;
}

void con_skip(void)
{
	uart_putc('\n');
}

void con_str(const char *label, const char *s)
{
	uart_puts(label);
	uart_puts(": ");
	uart_puts(s);
	uart_putc('\n');
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
	uart_puts(label);
	uart_puts(": ");
	uart_puts(a);
	fmt_hex(buf, va, 8);
	uart_puts(buf);
	if (b) {
		uart_putc(' ');
		uart_puts(b);
		fmt_hex(buf, vb, 8);
		uart_puts(buf);
	}
	uart_putc('\n');
}

#else
#define SCALE		3
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		24
#define LABEL_COLS	8

volatile uint32_t *con_fb = (volatile uint32_t *)FB_BASE;
static uint32_t con_bg;
static int row;

void con_clear(uint32_t bg)
{
	for (int y = 0; y < FB_H; y++) {
		volatile uint32_t *line = con_fb + y * (FB_STRIDE / 4);
		for (int x = 0; x < FB_W; x++)
			line[x] = bg;
	}
	con_bg = bg;
	row = 0;
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = MARGIN + row * CH_H;
	if (x0 + CH_W > FB_W || y0 + CH_H > FB_H)
		return;
	for (int gy = 0; gy < 16; gy++)
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? fg : con_bg;
			for (int sy = 0; sy < SCALE; sy++) {
				volatile uint32_t *p = con_fb + (y0 + gy * SCALE + sy) * (FB_STRIDE / 4)
						       + x0 + gx * SCALE;
				for (int sx = 0; sx < SCALE; sx++)
					p[sx] = c;
			}
		}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	while (*s)
		put_char(col++, *s++, fg);
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

void con_skip(void)
{
	row++;
}

void con_str(const char *label, const char *s)
{
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	row++;
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	row++;
}
#endif /* C21Y */
