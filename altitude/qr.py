"""A QR code (ISO/IEC 18004) for a short text such as a link: byte mode, error correction level M."""
from __future__ import annotations

# Per version 1-40 at level M: error-correction codewords per block, and the number of blocks.
_ECC = (10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
        26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28)
_BLOCKS = (1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
           17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49)
_LEVEL_M = 0  # the two format bits for level M
_MASKS = (lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
          lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
          lambda r, c: r * c % 2 + r * c % 3 == 0, lambda r, c: (r * c % 2 + r * c % 3) % 2 == 0,
          lambda r, c: ((r + c) % 2 + r * c % 3) % 2 == 0)


def _alignment(version: int) -> list[int]:
    if version == 1:
        return []
    count = version // 7 + 2
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    return [6] + [version * 4 + 10 - i * step for i in reversed(range(count - 1))]


def _raw_codewords(version: int) -> int:
    modules = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        modules -= (25 * count - 10) * count - 55
        if version >= 7:
            modules -= 36
    return modules // 8


def _multiply(x: int, y: int) -> int:
    """Product in GF(2^8) modulo the QR polynomial x^8 + x^4 + x^3 + x^2 + 1."""
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _remainder(data: list[int], degree: int) -> list[int]:
    divisor, root = [0] * (degree - 1) + [1], 1
    for _ in range(degree):
        for j in range(degree):
            divisor[j] = _multiply(divisor[j], root) ^ (divisor[j + 1] if j + 1 < degree else 0)
        root = _multiply(root, 2)
    result = [0] * degree
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coefficient in enumerate(divisor):
            result[i] ^= _multiply(coefficient, factor)
    return result


def _codewords(data: bytes) -> tuple[int, list[int]]:
    """The smallest version that holds `data`, and its interleaved data and error-correction codewords."""
    for version in range(1, 41):
        blocks, ecc = _BLOCKS[version - 1], _ECC[version - 1]
        capacity = _raw_codewords(version) - blocks * ecc
        length_bits = 8 if version < 10 else 16
        if 4 + length_bits + 8 * len(data) <= capacity * 8:
            break
    else:
        raise ValueError("Text is too long for a QR code.")
    bits = f"0100{len(data):0{length_bits}b}" + "".join(f"{byte:08b}" for byte in data)
    bits += "0" * min(4, capacity * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    words = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    words += [(0xEC, 0x11)[i % 2] for i in range(capacity - len(words))]
    short, long_count = divmod(capacity, blocks)
    split, start = [], 0
    for index in range(blocks):
        size = short + (index >= blocks - long_count)
        split.append(words[start:start + size])
        start += size
    checks = [_remainder(block, ecc) for block in split]
    interleaved = [block[i] for i in range(short + 1) for block in split if i < len(block)]
    return version, interleaved + [block[i] for i in range(ecc) for block in checks]


def _bch(value: int, generator: int, bits: int) -> int:
    remainder = value << bits
    for shift in reversed(range(bits + 1)):
        if remainder >> (shift + generator.bit_length() - 1) & 1:
            remainder ^= generator << shift
    return value << bits | remainder


def matrix(text: str) -> list[list[bool]]:
    """The modules of `text`'s QR code, True for dark, without the quiet zone around it."""
    version, codewords = _codewords(text.encode())
    size = version * 4 + 17
    grid = [[False] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]

    def put(row: int, column: int, dark: bool) -> None:
        grid[row][column], fixed[row][column] = dark, True

    for i in range(size):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for row, column in ((3, 3), (3, size - 4), (size - 4, 3)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if 0 <= row + dy < size and 0 <= column + dx < size:
                    put(row + dy, column + dx, max(abs(dy), abs(dx)) not in (2, 4))
    centres = _alignment(version)
    last = len(centres) - 1
    for i, row in enumerate(centres):
        for j, column in enumerate(centres):
            if (i, j) not in ((0, 0), (0, last), (last, 0)):
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        put(row + dy, column + dx, max(abs(dy), abs(dx)) != 1)
    if version >= 7:
        bits = _bch(version, 0x1F25, 12)
        for i in range(18):
            dark = bool(bits >> i & 1)
            put(size - 11 + i % 3, i // 3, dark)
            put(i // 3, size - 11 + i % 3, dark)

    def format_bits(target: list[list[bool]], mask: int) -> None:
        bits = _bch(_LEVEL_M << 3 | mask, 0x537, 10) ^ 0x5412
        first = [(i, 8) for i in range(6)] + [(7, 8), (8, 8), (8, 7)] + [(8, 14 - i) for i in range(9, 15)]
        second = [(8, size - 1 - i) for i in range(8)] + [(size - 15 + i, 8) for i in range(8, 15)]
        for i in range(15):
            for row, column in (first[i], second[i]):
                target[row][column], fixed[row][column] = bool(bits >> i & 1), True
        target[size - 8][8] = fixed[size - 8][8] = True

    format_bits(grid, 0)  # reserves the format area; each mask writes its own below
    stream = "".join(f"{word:08b}" for word in codewords)
    index = 0
    for right in range(size - 1, 0, -2):
        right -= right <= 6  # the vertical timing column is skipped
        upward = (right + 1) & 2 == 0
        for step in range(size):
            row = size - 1 - step if upward else step
            for column in (right, right - 1):
                if not fixed[row][column]:
                    grid[row][column] = index < len(stream) and stream[index] == "1"
                    index += 1

    candidates = []
    for mask in range(8):
        candidate = [[grid[r][c] ^ (not fixed[r][c] and _MASKS[mask](r, c)) for c in range(size)] for r in range(size)]
        format_bits(candidate, mask)
        candidates.append(candidate)
    return min(candidates, key=_penalty)


def _penalty(grid: list[list[bool]]) -> int:
    size, score = len(grid), 0
    lines = grid + [list(column) for column in zip(*grid)]
    finder = ([True, False, True, True, True, False, True, False, False, False, False],
              [False, False, False, False, True, False, True, True, True, False, True])
    for line in lines:
        run, previous = 0, None
        for module in line + [None]:
            if module == previous:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, previous = 1, module
        padded = [False] * 4 + line + [False] * 4
        score += 40 * sum(padded[i:i + 11] in finder for i in range(len(padded) - 10))
    score += 3 * sum(grid[r][c] == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]
                     for r in range(size - 1) for c in range(size - 1))
    dark = sum(map(sum, grid))
    score += 10 * (abs(dark * 20 - size * size * 10) // (size * size))
    return score


def terminal(text: str) -> str:
    """`text`'s QR code drawn with half blocks, two module rows per line, in the 256-colour palette's pure
    black on pure white, which terminals do not re-theme, with the four-module quiet zone a camera needs."""
    modules = matrix(text)
    quiet = 4
    width = len(modules) + 2 * quiet
    rows = [[False] * width] * quiet + [[False] * quiet + row + [False] * quiet for row in modules] \
        + [[False] * width] * (quiet + 1)
    glyphs = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}
    return "\n".join("\x1b[38;5;16;48;5;231m" + "".join(glyphs[pair] for pair in zip(rows[i], rows[i + 1])) + "\x1b[0m"
                     for i in range(0, len(rows) - 1, 2))
