"""Minimal QR Code generator (byte mode, versions 1-40) + PNG writer.

Pure Python standard library, no bpy. Follows ISO/IEC 18004; the structure
is based on the well known public-domain style reference algorithm by
Project Nayuki. Good enough for URLs, which is all Blender Drop needs.
"""

import struct
import zlib

ECC_LOW, ECC_MEDIUM, ECC_QUARTILE, ECC_HIGH = 0, 1, 2, 3
_FORMAT_BITS = {ECC_LOW: 1, ECC_MEDIUM: 0, ECC_QUARTILE: 3, ECC_HIGH: 2}

_ECC_CODEWORDS_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)
_NUM_ERROR_CORRECTION_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)


def _num_raw_data_modules(ver):
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _num_data_codewords(ver, ecl):
    return (_num_raw_data_modules(ver) // 8
            - _ECC_CODEWORDS_PER_BLOCK[ecl][ver] * _NUM_ERROR_CORRECTION_BLOCKS[ecl][ver])


def _gf_multiply(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_multiply(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_multiply(coef, factor)
    return result


class QrCode:
    """Immutable QR symbol. ``modules[y][x]`` is True for dark modules."""

    def __init__(self, data, ecl=ECC_MEDIUM, min_version=1, max_version=40):
        if isinstance(data, str):
            data = data.encode("utf-8")
        data = bytes(data)

        for ver in range(min_version, max_version + 1):
            count_bits = 8 if ver <= 9 else 16
            needed_bits = 4 + count_bits + len(data) * 8
            if needed_bits <= _num_data_codewords(ver, ecl) * 8:
                break
        else:
            raise ValueError("Data too long for a QR code")
        # Use the highest ECC level that still fits in this version.
        for better in (ECC_MEDIUM, ECC_QUARTILE, ECC_HIGH):
            if better > ecl and needed_bits <= _num_data_codewords(ver, better) * 8:
                ecl = better

        self.version = ver
        self.ecl = ecl
        self.size = ver * 4 + 17

        # Bit stream: byte mode indicator, length, data, terminator, padding.
        bits = []

        def append(value, length):
            for i in reversed(range(length)):
                bits.append((value >> i) & 1)

        append(0x4, 4)
        append(len(data), count_bits)
        for b in data:
            append(b, 8)
        capacity = _num_data_codewords(ver, ecl) * 8
        append(0, min(4, capacity - len(bits)))
        append(0, -len(bits) % 8)
        pad = 0xEC
        while len(bits) < capacity:
            append(pad, 8)
            pad ^= 0xEC ^ 0x11
        codewords = [0] * (len(bits) // 8)
        for i, bit in enumerate(bits):
            codewords[i >> 3] |= bit << (7 - (i & 7))

        size = self.size
        self.modules = [[False] * size for _ in range(size)]
        self._is_function = [[False] * size for _ in range(size)]
        self._draw_function_patterns()
        all_codewords = self._add_ecc_and_interleave(codewords)
        self._draw_codewords(all_codewords)

        best_mask, best_penalty = 0, None
        for mask in range(8):
            self._apply_mask(mask)
            self._draw_format_bits(mask)
            penalty = self._penalty_score()
            if best_penalty is None or penalty < best_penalty:
                best_mask, best_penalty = mask, penalty
            self._apply_mask(mask)  # XOR again to undo
        self.mask = best_mask
        self._apply_mask(best_mask)
        self._draw_format_bits(best_mask)
        del self._is_function

    # -- construction helpers ---------------------------------------------

    def _set_function(self, x, y, dark):
        self.modules[y][x] = dark
        self._is_function[y][x] = True

    def _draw_function_patterns(self):
        size = self.size
        for i in range(size):
            self._set_function(6, i, i % 2 == 0)
            self._set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self._set_function(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = self._alignment_positions()
        n = len(positions)
        for i in range(n):
            for j in range(n):
                if (i == 0 and j == 0) or (i == 0 and j == n - 1) or (i == n - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set_function(positions[i] + dx, positions[j] + dy,
                                           max(abs(dx), abs(dy)) != 1)
        self._draw_format_bits(0)  # reserve area, real bits drawn later
        self._draw_version()

    def _alignment_positions(self):
        ver = self.version
        if ver == 1:
            return []
        numalign = ver // 7 + 2
        step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
        result = [self.size - 7 - i * step for i in range(numalign - 1)] + [6]
        return list(reversed(result))

    def _draw_format_bits(self, mask):
        data = _FORMAT_BITS[self.ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        size = self.size

        def bit(i):
            return ((bits >> i) & 1) != 0

        for i in range(0, 6):
            self._set_function(8, i, bit(i))
        self._set_function(8, 7, bit(6))
        self._set_function(8, 8, bit(7))
        self._set_function(7, 8, bit(8))
        for i in range(9, 15):
            self._set_function(14 - i, 8, bit(i))
        for i in range(0, 8):
            self._set_function(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set_function(8, size - 15 + i, bit(i))
        self._set_function(8, size - 8, True)  # always-dark module

    def _draw_version(self):
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            dark = ((bits >> i) & 1) != 0
            a = self.size - 11 + i % 3
            b = i // 3
            self._set_function(a, b, dark)
            self._set_function(b, a, dark)

    def _add_ecc_and_interleave(self, data):
        ver, ecl = self.version, self.ecl
        numblocks = _NUM_ERROR_CORRECTION_BLOCKS[ecl][ver]
        blockecclen = _ECC_CODEWORDS_PER_BLOCK[ecl][ver]
        rawcodewords = _num_raw_data_modules(ver) // 8
        numshortblocks = numblocks - rawcodewords % numblocks
        shortblocklen = rawcodewords // numblocks
        divisor = _rs_divisor(blockecclen)
        blocks = []
        k = 0
        for i in range(numblocks):
            datlen = shortblocklen - blockecclen + (0 if i < numshortblocks else 1)
            dat = data[k:k + datlen]
            k += datlen
            ecc = _rs_remainder(dat, divisor)
            if i < numshortblocks:
                dat = dat + [0]
            blocks.append(dat + ecc)
        result = []
        for i in range(len(blocks[0])):
            for j, blk in enumerate(blocks):
                if i != shortblocklen - blockecclen or j >= numshortblocks:
                    result.append(blk[i])
        return result

    def _draw_codewords(self, data):
        size = self.size
        i = 0
        right = size - 1
        total_bits = len(data) * 8
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self._is_function[y][x] and i < total_bits:
                        self.modules[y][x] = ((data[i >> 3] >> (7 - (i & 7))) & 1) != 0
                        i += 1
            right -= 2

    _MASKS = (
        lambda x, y: (x + y) % 2 == 0,
        lambda x, y: y % 2 == 0,
        lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0,
        lambda x, y: (x // 3 + y // 2) % 2 == 0,
        lambda x, y: x * y % 2 + x * y % 3 == 0,
        lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
    )

    def _apply_mask(self, mask):
        fn = self._MASKS[mask]
        for y in range(self.size):
            row = self.modules[y]
            func_row = self._is_function[y]
            for x in range(self.size):
                if not func_row[x] and fn(x, y):
                    row[x] = not row[x]

    def _penalty_score(self):
        size = self.size
        mods = self.modules
        score = 0
        lines = [mods[y] for y in range(size)] + [[mods[y][x] for y in range(size)] for x in range(size)]
        finder_a = [True, False, True, True, True, False, True, False, False, False, False]
        finder_b = finder_a[::-1]
        for line in lines:
            run = 1
            for i in range(1, size):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run = 1
            if run >= 5:
                score += 3 + run - 5
            for i in range(size - 10):
                seg = line[i:i + 11]
                if seg == finder_a or seg == finder_b:
                    score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                c = mods[y][x]
                if c == mods[y][x + 1] == mods[y + 1][x] == mods[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(1 for m in row if m) for row in mods)
        total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        score += k * 10
        return score

    # -- output ------------------------------------------------------------

    def to_png(self, scale=8, border=4):
        """Return PNG bytes (8-bit grayscale)."""
        side = (self.size + border * 2) * scale
        rows = []
        white_row = b"\x00" + b"\xff" * side
        for _ in range(border * scale):
            rows.append(white_row)
        for y in range(self.size):
            line = bytearray(b"\xff" * (border * scale))
            for x in range(self.size):
                line += (b"\x00" if self.modules[y][x] else b"\xff") * scale
            line += b"\xff" * (border * scale)
            row = b"\x00" + bytes(line)
            rows.extend([row] * scale)
        for _ in range(border * scale):
            rows.append(white_row)
        raw = b"".join(rows)

        def chunk(tag, payload):
            return (struct.pack(">I", len(payload)) + tag + payload
                    + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

        header = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

    def to_text(self, border=2):
        """Terminal-friendly rendering using half-block characters."""
        size = self.size

        def dark(x, y):
            return 0 <= x < size and 0 <= y < size and self.modules[y][x]

        out = []
        for y in range(-border, size + border, 2):
            line = ""
            for x in range(-border, size + border):
                top, bottom = dark(x, y), dark(x, y + 1)
                # dark modules are drawn as spaces on a light background
                line += {(False, False): "█", (True, False): "▄",
                         (False, True): "▀", (True, True): " "}[(top, bottom)]
            out.append(line)
        return "\n".join(out)


def write_png(text, path, scale=8, border=4):
    qr = QrCode(text)
    with open(path, "wb") as handle:
        handle.write(qr.to_png(scale=scale, border=border))
    return qr
