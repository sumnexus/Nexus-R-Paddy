# Copied from the Agri Robotics Playground's Python port (agriplay/util.py) for the Paddy API Farm; unchanged below.
"""Seeded randomness, noise, math helpers, dates and small containers.

Everything in the simulation is deterministic for a given seed. The random number
generator (mulberry32) and the integer hashes are bit-for-bit the same as the web
version's, so the same seed draws the same weather. The transcendental functions come
from Python's ``math`` module (the web version carries its own fdlibm copies so that
every browser agrees); results can differ from the web version in the last bit, and a
long simulation grows those bits into its own numbers.
"""
from __future__ import annotations

from functools import lru_cache as _lru_cache
import datetime as _dt
import math
import struct
from functools import lru_cache

M32 = 0xFFFFFFFF
DAY = 86400
HOUR = 3600
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
               'September', 'October', 'November', 'December']
WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
INF = math.inf
_F32 = struct.Struct('<f')


class Obj(dict):
    """A dict whose keys are also attributes, like a JavaScript object.

    Missing keys read as ``None`` (JavaScript's ``undefined``). Crops, pests, setups and
    log entries are ``Obj`` so they stay plain JSON-compatible data. The instance's attribute
    dictionary is the dict itself, so reading a key as an attribute is as fast as any attribute.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.__dict__ = self

    def __getattr__(self, k):  # only reached for missing keys
        if k.startswith('__'):
            raise AttributeError(k)
        return None

    def __reduce__(self):
        return (Obj, (dict(self),))

    def copy(self):
        return Obj(self)


def to_obj(x):
    """Recursively turn dicts (and dicts inside lists) into ``Obj``."""
    if isinstance(x, dict):
        return Obj({k: to_obj(v) for k, v in dict.items(x)})
    if isinstance(x, list):
        return [to_obj(v) for v in x]
    return x


def to_plain(x):
    """Recursively turn ``Obj``/tuples into plain dicts/lists (for JSON)."""
    if isinstance(x, dict):
        return {k: to_plain(v) for k, v in dict.items(x)}
    if isinstance(x, (list, tuple)):
        return [to_plain(v) for v in x]
    return x


# ── 32-bit integer hashes (identical to the web version) ──
def hash32(x: int) -> int:
    """lowbias32: a 32-bit integer hash used to derive independent seeds."""
    x = (x & M32) ^ 0x9E3779B9
    x ^= x >> 16
    x = (x * 0x21F0AAAD) & M32
    x ^= x >> 15
    x = (x * 0x735A2D97) & M32
    x ^= x >> 15
    return x


@_lru_cache(maxsize=16384)
def str_hash(s: str) -> int:
    """FNV-1a over UTF-16 code units (what JavaScript's charCodeAt sees). Cached: the same short tags recur."""
    h = 2166136261
    b = s.encode('utf-16-le')
    for i in range(0, len(b), 2):
        h ^= b[i] | (b[i + 1] << 8)
        h = (h * 16777619) & M32
    return h


def _num_key(v) -> int:
    # Math.floor(v * 1000003) | 0, then >>> 0 inside hash32
    try:
        x = v * 1000003
    except TypeError:
        return 0
    if x != x or x in (INF, -INF):
        return 0
    return math.floor(x) & M32


def hash_n(*vals) -> int:
    """Hash any mix of numbers and strings into one unsigned 32-bit integer (hash32 inlined: this is hot)."""
    h = 0x811C9DC5
    for v in vals:
        n = str_hash(v) if v.__class__ is str else _num_key(v)
        # x = hash32(n)
        x = (n & 0xFFFFFFFF) ^ 0x9E3779B9
        x ^= x >> 16
        x = (x * 0x21F0AAAD) & 0xFFFFFFFF
        x ^= x >> 15
        x = (x * 0x735A2D97) & 0xFFFFFFFF
        x ^= x >> 15
        # h = hash32(h ^ x)
        x = (h ^ x) ^ 0x9E3779B9
        x ^= x >> 16
        x = (x * 0x21F0AAAD) & 0xFFFFFFFF
        x ^= x >> 15
        x = (x * 0x735A2D97) & 0xFFFFFFFF
        h = x ^ (x >> 15)
    return h


def hash01(*vals) -> float:
    """[0, 1) from integers — stable per-object randomness (e.g. plant jitter)."""
    return hash_n(*vals) / 4294967296


class RNG:
    """mulberry32 with the web version's helpers (same seed ⇒ same draws)."""

    __slots__ = ('seed', '_a', '_spare')

    def __init__(self, seed: int):
        self.seed = int(seed) & M32
        self._a = self.seed
        self._spare = None

    def random(self) -> float:
        a = (self._a + 0x6D2B79F5) & M32
        self._a = a
        t = ((a ^ (a >> 15)) * (1 | a)) & M32
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & M32)) & M32) ^ t
        return ((t ^ (t >> 14)) & M32) / 4294967296

    def range(self, a: float, b: float) -> float:
        return a + (b - a) * self.random()

    def int(self, a: int, b: int) -> int:
        return a + math.floor((b - a + 1) * self.random())

    def chance(self, p: float) -> bool:
        return self.random() < p

    def pick(self, arr):
        return arr[math.floor(self.random() * len(arr))]

    def normal(self, mu: float = 0.0, sd: float = 1.0) -> float:
        if self._spare is not None:
            s = self._spare
            self._spare = None
            return mu + sd * s
        while True:
            u = self.random() * 2 - 1
            v = self.random() * 2 - 1
            s = u * u + v * v
            if 0 < s < 1:
                break
        m = math.sqrt(-2 * math.log(s) / s)
        self._spare = v * m
        return mu + sd * u * m

    def gamma(self, k: float, theta: float = 1.0) -> float:
        """Marsaglia–Tsang gamma sampler (shape k, scale theta)."""
        if k < 1:
            u = self.random()
            return self.gamma(1 + k, theta) * (u ** (1 / k))
        d = k - 1 / 3
        c = 1 / math.sqrt(9 * d)
        while True:
            while True:
                x = self.normal()
                v = 1 + c * x
                if v > 0:
                    break
            v = v * v * v
            u = self.random()
            if u < 1 - 0.0331 * x * x * x * x:
                return d * v * theta
            if math.log(u) < 0.5 * x * x + d * (1 - v + math.log(v)):
                return d * v * theta

    def shuffle(self, arr: list) -> list:
        for i in range(len(arr) - 1, 0, -1):
            j = math.floor(self.random() * (i + 1))
            arr[i], arr[j] = arr[j], arr[i]
        return arr

    def fork(self, salt) -> 'RNG':
        return RNG(hash_n(self.seed, salt))


# ── smooth 2-D value noise ──
def make_noise_2d(seed: int):
    """Smooth 2-D value noise in [0, 1] with a seeded lattice (same lattice as the web version)."""
    r = RNG(seed)
    vals = []
    p = []
    for i in range(256):
        p.append(i)
        vals.append(_F32.unpack(_F32.pack(r.random()))[0])  # a Float32Array in the web version
    r.shuffle(p)
    perm = [p[i & 255] for i in range(512)]

    def fade(t):
        return t * t * t * (t * (t * 6 - 15) + 10)

    def noise(x, y):
        xi, yi = math.floor(x), math.floor(y)
        xf, yf = x - xi, y - yi
        X, Y = xi & 255, yi & 255
        v00 = vals[perm[X + perm[Y]]]
        v10 = vals[perm[X + 1 + perm[Y]]]
        v01 = vals[perm[X + perm[Y + 1]]]
        v11 = vals[perm[X + 1 + perm[Y + 1]]]
        u, v = fade(xf), fade(yf)
        return lerp(lerp(v00, v10, u), lerp(v01, v11, u), v)

    return noise


def fbm(noise, x, y, octaves=4, lacunarity=2.0, gain=0.5):
    amp, freq, s, norm = 1.0, 1.0, 0.0, 0.0
    for _ in range(octaves):
        s += amp * noise(x * freq, y * freq)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return s / norm


# ── small math ──
def clamp(x, a, b):
    return a if x < a else b if x > b else x


def lerp(a, b, t):
    return a + (b - a) * t


def inv_lerp(a, b, x):
    return clamp((x - a) / (b - a), 0.0, 1.0)


def smoothstep(a, b, x):
    t = inv_lerp(a, b, x)
    return t * t * (3 - 2 * t)


def sigmoid(x):
    if x < -700:
        return 0.0
    return 1 / (1 + math.exp(-x))


def mean(arr):
    arr = list(arr)
    return sum(arr) / len(arr) if arr else 0.0


def dist2(ax, az, bx, bz):
    return math.sqrt((ax - bx) ** 2 + (az - bz) ** 2)


def hypot(a, b):
    return math.sqrt(a * a + b * b)


def js_round(x: float) -> int:
    """JavaScript's Math.round (halves go up, also for negative numbers)."""
    return math.floor(x + 0.5)


# ── dates (UTC, so time zones never shift the calendar) ──
class DateInfo(tuple):
    __slots__ = ()
    y = property(lambda s: s[0])
    m = property(lambda s: s[1])
    d = property(lambda s: s[2])
    dow = property(lambda s: s[3])
    doy = property(lambda s: s[4])
    label = property(lambda s: s[5])
    short = property(lambda s: s[6])


@lru_cache(maxsize=8192)
def date_info(start_year: int, start_month: int, day_index: int) -> DateInfo:
    base = _dt.date(int(start_year), int(start_month), 1)
    d = base + _dt.timedelta(days=int(day_index))
    dow = (d.weekday() + 1) % 7
    doy = d.timetuple().tm_yday
    label = f'{WEEKDAYS[dow]} {d.day} {MONTHS[d.month - 1]} {d.year}'
    return DateInfo((d.year, d.month, d.day, dow, doy, label, f'{d.day} {MONTHS[d.month - 1]}'))


def fmt_clock(hours: float) -> str:
    h = math.floor(hours) % 24
    m = math.floor((hours - math.floor(hours)) * 60)
    return f'{h:02d}:{m:02d}'


def fmt_duration(sec: float) -> str:
    if sec < 90:
        return f'{js_round(sec)} s'
    if sec < 5400:
        return f'{js_round(sec / 60)} min'
    if sec < 2 * DAY:
        h = math.floor(sec / 3600)
        m = js_round((sec % 3600) / 60)
        return f'{h} h' + (f' {m} min' if m else '')
    return f'{sec / DAY:.1f} days'


def _group_in(int_part: str) -> str:
    # Indian digit grouping: 12,34,567
    if len(int_part) <= 3:
        return int_part
    head, tail = int_part[:-3], int_part[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ','.join(groups) + ',' + tail


def fmt(n, dp: int = 0) -> str:
    """A number with Indian digit grouping (like toLocaleString('en-IN'))."""
    try:
        if n is None or not math.isfinite(n):
            return '–'
    except TypeError:
        return '–'
    neg = n < 0
    s = f'{abs(n):.{dp}f}'
    if '.' in s:
        a, b = s.split('.')
        s = _group_in(a) + '.' + b
    else:
        s = _group_in(s)
    if neg and any(ch not in '0.,' for ch in s):
        s = '-' + s
    return s


class Ring:
    """Fixed-capacity ring buffer of numbers (for time-series charts)."""

    __slots__ = ('cap', 'data', 'n', 'head')

    def __init__(self, cap: int):
        self.cap = cap
        self.data = [0.0] * cap
        self.n = 0
        self.head = 0

    def push(self, v):
        self.data[self.head] = v
        self.head = (self.head + 1) % self.cap
        if self.n < self.cap:
            self.n += 1

    def to_list(self):
        start = (self.head - self.n + self.cap) % self.cap
        return [self.data[(start + i) % self.cap] for i in range(self.n)]

    def last(self):
        return self.data[(self.head - 1 + self.cap) % self.cap] if self.n else math.nan

    def clear(self):
        self.n = 0
        self.head = 0
