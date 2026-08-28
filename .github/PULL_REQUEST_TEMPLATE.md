## What this changes

<!-- The behaviour that is different afterwards. One paragraph. -->

## Why

<!-- The cause you fixed, not the symptom you saw. If this is a bug fix, say
     which callers route through the code you touched. -->

## How it was verified

<!-- What you actually ran, and what it said. "Tests pass" is not verification;
     naming the check that would fail without this change is. -->

```
```

## Checklist

- [ ] `uv run pytest -q` in `janus_server/`
- [ ] `pnpm test` and `pnpm exec tsc --noEmit` in `janus/`
- [ ] Non-trivial logic leaves behind one runnable check that fails if it breaks
- [ ] No test was weakened to make this pass
- [ ] Docs changed in this commit if a default, provider, or safety boundary moved
- [ ] Packaged and launched on a Mac, if this touches packaging, MLX, or the model runtime

<!-- Larger than a small fix? Open an issue first — I may already be mid-rewrite
     of the area. See CONTRIBUTING.md. -->
