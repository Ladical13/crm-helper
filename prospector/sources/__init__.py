"""Prospect sources. Each exposes SEGMENTS, count(segment) and pull(segment)."""
from . import cdos, dora

REGISTRY = {'dora': dora, 'cdos': cdos}


def resolve(name):
    """'dora:hoa' -> (module, segment_key, segment_meta)."""
    if ':' not in name:
        raise KeyError(f'segment must look like source:segment, got {name!r}')
    src, seg = name.split(':', 1)
    if src not in REGISTRY:
        raise KeyError(f'unknown source {src!r} (have {", ".join(sorted(REGISTRY))})')
    mod = REGISTRY[src]
    if seg not in mod.SEGMENTS:
        raise KeyError(f'unknown {src} segment {seg!r} '
                       f'(have {", ".join(sorted(mod.SEGMENTS))})')
    return mod, seg, mod.SEGMENTS[seg]


def all_segments(defaults_only=False):
    """[(name, module, meta)] across every source."""
    out = []
    for src, mod in sorted(REGISTRY.items()):
        for seg, meta in mod.SEGMENTS.items():
            if defaults_only and meta.get('default') is False:
                continue
            out.append((f'{src}:{seg}', mod, meta))
    return out
