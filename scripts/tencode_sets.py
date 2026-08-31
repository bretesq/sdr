#!/usr/bin/env python3
"""Load and resolve the radio code sets in data/tencodes/.

A code set is keyed by (agency, discipline), not agency alone: EBR Sheriff and
EBR Fire share a parish but not a codebook. `talkgroups.cat` supplies the
agency and `talkgroups.tag` the discipline, and index.json maps that pair to a
set id.

Sets compose through `extends`, child overriding parent. An agency set that is
empty apart from its `extends` is a valid, working configuration — the chain
does the work, and the file is the destination for a code confirmed later.

A code absent from the whole chain resolves to nothing, and the caller renders
it un-expanded. Nothing here ever invents a meaning.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from functools import lru_cache

DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'tencodes')

TABLES = ('ten', 'signal', 'response')


def _sets_dir(root: str) -> str:
    return os.path.join(root, 'sets')


def all_set_ids(root: str = DATA_ROOT) -> list[str]:
    d = _sets_dir(root)
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith('.json'))


@lru_cache(maxsize=None)
def load_set(set_id: str, root: str = DATA_ROOT) -> dict:
    """Read one set file verbatim. Raises if the id does not exist."""
    with open(os.path.join(_sets_dir(root), set_id + '.json')) as f:
        return json.load(f)


@lru_cache(maxsize=None)
def load_index(root: str = DATA_ROOT) -> tuple[dict, ...]:
    with open(os.path.join(root, 'index.json')) as f:
        return tuple(json.load(f))


def chain_of(set_id: str, root: str = DATA_ROOT) -> list[str]:
    """The `extends` chain, most specific first. Raises on a cycle."""
    chain: list[str] = []
    seen: set[str] = set()
    cur: str | None = set_id
    while cur:
        if cur in seen:
            raise ValueError(f'cyclic extends chain at {cur}: {chain}')
        seen.add(cur)
        chain.append(cur)
        cur = load_set(cur, root).get('extends')
    return chain


def resolve_set_id(cat: str | None, tag: str | None, root: str = DATA_ROOT) -> str:
    """Map a talkgroup's (cat, tag) to a set id. First matching rule wins.

    A missing cat or tag matches only the catch-all, which is why index.json is
    required to end with one — enforced by test_index_has_a_catch_all_rule.
    """
    c = cat or ''
    t = tag or ''
    for rule in load_index(root):
        if fnmatch.fnmatch(c, rule['cat']) and fnmatch.fnmatch(t, rule['tag']):
            return rule['set']
    raise ValueError('index.json has no catch-all rule')


def resolve(set_id: str, root: str = DATA_ROOT) -> dict:
    """Flatten the `extends` chain into one lookup table. Child wins."""
    chain = chain_of(set_id, root)
    out: dict = {'id': set_id,
                 'name': load_set(set_id, root).get('name', set_id)}
    for table in TABLES:
        merged: dict = {}
        for sid in reversed(chain):          # parent first, child overwrites
            merged.update(load_set(sid, root).get(table, {}))
        out[table] = merged
    return out


def set_rev(resolved: dict, extractor_version: str) -> str:
    """Short hash over everything that can change extraction output.

    `codes_set_id` alone is not enough to find stale rows: correcting a meaning
    inside an existing set leaves the id identical, so --only-stale would skip
    every affected row and the correction would never reach codes_text or
    call_codes. Since meanings get corrected repeatedly as sets are sourced,
    that is the common case.

    `common` is excluded deliberately — it affects rendering only, never stored
    output, so toggling it needs no backfill.
    """
    payload = {
        'v': extractor_version,
        'id': resolved['id'],
        'tables': {t: {k: resolved[t][k]['meaning'] for k in sorted(resolved[t])}
                   for t in TABLES},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(blob).hexdigest()[:12]
