# Copied from the Agri Robotics Playground's Python port (agriplay/forests.py) for the Paddy API Farm; unchanged below.
"""The shipped forests: conversion from the web version's pack and loading.

The web version keeps its seven trained forests in ``forestdata.js`` as base64: per tree a
node count, then the nodes in preorder (left child next): a feature byte (255 = leaf), and
for a split its float32 threshold and the varint offset of its right child, for a leaf its
float32 value(s) (P(class 1) for two classes). ``convert`` decodes that pack into flat NumPy
arrays (``models/forests.npz``) plus a metadata file (``models/forests.json``), which
``load_pack`` reads quickly.

Refresh the Python models from a newer web build with::

    python -m agriplay.forests path/to/forestdata.js
"""
from __future__ import annotations

import base64
import json
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(HERE, 'models')
NPZ = os.path.join(MODELS_DIR, 'forests.npz')
META = os.path.join(MODELS_DIR, 'forests.json')


def read_pack_js(path):
    """The JSON object inside forestdata.js (``AG.forestData = {...};``)."""
    src = open(path, encoding='utf-8').read()
    m = re.search(r'AG\.forestData\s*=\s*', src)
    if not m:
        raise ValueError(f'{path}: no AG.forestData found')
    body = src[m.end():].rstrip()
    if body.endswith(';'):
        body = body[:-1]
    return json.loads(body)


def decode_forest(spec):
    """Decode one model of the pack into node-local arrays (all trees concatenated)."""
    data = base64.b64decode(spec['data'])
    C = spec['nClasses'] if spec['type'] == 'cls' else 1
    pos = 0
    n_bytes = len(data)
    feats, thrs, lefts, rights, vals, roots = [], [], [], [], [], []
    off = 0
    unpack_f = struct.Struct('<f').unpack_from
    for _ in range(spec['nTrees']):
        # varint node count
        v, sh = 0, 0
        while True:
            b = data[pos]
            pos += 1
            v += (b & 127) << sh
            sh += 7
            if not b & 128:
                break
        n = v
        feat = [0] * n
        thr = [0.0] * n
        left = [-1] * n
        right = [-1] * n
        val = [[0.0] * C for _ in range(n)]
        for i in range(n):
            f = data[pos]
            pos += 1
            if f == 255:
                feat[i] = -1
                if spec['type'] == 'cls' and C == 2:
                    p = unpack_f(data, pos)[0]
                    pos += 4
                    val[i] = [1 - p, p]
                else:
                    for c in range(C):
                        val[i][c] = unpack_f(data, pos)[0]
                        pos += 4
            else:
                feat[i] = f
                thr[i] = unpack_f(data, pos)[0]
                pos += 4
                v, sh = 0, 0
                while True:
                    b = data[pos]
                    pos += 1
                    v += (b & 127) << sh
                    sh += 7
                    if not b & 128:
                        break
                left[i] = off + i + 1
                right[i] = off + i + v
        roots.append(off)
        feats += feat
        thrs += thr
        lefts += left
        rights += right
        vals += val
        off += n
    if pos != n_bytes:
        raise ValueError(f"forest {spec.get('id')}: {n_bytes - pos} bytes left over")
    return (np.asarray(feats, np.int16), np.asarray(thrs, np.float32), np.asarray(lefts, np.int32),
            np.asarray(rights, np.int32), np.asarray(vals, np.float64).reshape(-1, C), np.asarray(roots, np.int32))


def _depth(feat, left, right, roots):
    depth = np.zeros(len(feat), np.int32)
    best = 0
    for nd in np.nonzero(feat >= 0)[0]:
        d = depth[nd] + 1
        depth[left[nd]] = d
        depth[right[nd]] = d
        best = max(best, int(d))
    return best


def convert(js_path, out_dir=MODELS_DIR):
    pack = read_pack_js(js_path)
    os.makedirs(out_dir, exist_ok=True)
    arrays, meta = {}, {'version': pack.get('version', ''), 'built': pack.get('built', ''), 'models': {}}
    for mid, spec in pack['models'].items():
        spec = dict(spec, id=mid)
        feat, thr, left, right, val, roots = decode_forest(spec)
        for k, a in (('feat', feat), ('thr', thr), ('left', left), ('right', right), ('val', val), ('roots', roots)):
            arrays[f'{mid}__{k}'] = a
        m = {k: v for k, v in spec.items() if k != 'data'}
        m['nodes'] = int(len(feat))
        m['depth'] = _depth(feat, left, right, roots)
        meta['models'][mid] = m
        print(f'  {mid}: {spec["nTrees"]} trees, {len(feat):,} nodes, depth {m["depth"]}')
    np.savez_compressed(os.path.join(out_dir, 'forests.npz'), **arrays)
    with open(os.path.join(out_dir, 'forests.json'), 'w') as f:
        json.dump(meta, f, indent=1)
    print(f"wrote {os.path.join(out_dir, 'forests.npz')} ({os.path.getsize(os.path.join(out_dir, 'forests.npz')) / 1e6:.1f} MB)")
    return meta


_cache = {}


def load_pack(npz=NPZ, meta_path=META):
    """{'version', 'built', 'models': {id: (meta, arrays)}} — cached per process."""
    key = (npz, meta_path)
    if key in _cache:
        return _cache[key]
    with open(meta_path) as f:
        meta = json.load(f)
    z = np.load(npz)
    models = {}
    for mid, m in meta['models'].items():
        arr = {k: z[f'{mid}__{k}'] for k in ('feat', 'thr', 'left', 'right', 'val', 'roots')}
        models[mid] = (m, arr)
    out = {'version': meta.get('version', ''), 'built': meta.get('built', ''), 'models': models}
    _cache[key] = out
    return out


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else MODELS_DIR)
