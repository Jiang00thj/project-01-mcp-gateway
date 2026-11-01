# Raw benchmark results

Every accuracy number in the top-level `README.md` points back to a file in
this directory. Nothing here is hand-edited: each file is the verbatim
`--out` payload of one `bench.py select` run.

Keeping the raw runs is what lets a reviewer check the claim instead of
trusting it. If you change a description, a case, or the provider, re-run and
diff — do not adjust the README by hand.

## Naming

```
v2_<model>_<arm>[_<n>].json

  model   flash = deepseek-flash, pro = deepseek-v4-pro
  arm     orig  = descriptions as written in servers/  (control group)
          impr  = descriptions from benchkit/descriptions.py
  n       tool budget for that run; omitted means all 12
```

`thinking:off` in every payload is deliberate: reasoning tokens eat the budget
and make the tool name get truncated, which is a different failure mode from
the one being measured.

## Selection accuracy (63 cases, 12 tools)

| File | Model | Arm | Tools | Cases | Accuracy |
|---|---|---|---|---|---|
| `v2_flash_orig.json` | deepseek-flash | original | 12 | 63 | 77.8% |
| `v2_flash_impr.json` | deepseek-flash | improved | 12 | 63 | 92.1% |
| `v2_pro_orig.json` | deepseek-v4-pro | original | 12 | 63 | 79.4% |
| `v2_pro_impr.json` | deepseek-v4-pro | improved | 12 | 63 | 100.0% |

The 4-row comparison is the whole argument:

- bigger model alone: 77.8% -> 79.4% (**+1.6pt, not significant**)
- same small model, better descriptions: 77.8% -> 92.1% (**+14.3pt, McNemar p=0.0117**)

So the bottleneck was the descriptions, not model scale.

## Degradation curve (does the gain survive more tools?)

| Tools | Cases | original | improved | Gap |
|---|---|---|---|---|
| 3 | 17 | 100.0% (`v2_flash_orig3`) | 100.0% (`v2_flash_impr3`) | 0 |
| 6 | 37 | 89.2% (`v2_flash_orig6`) | 100.0% (`v2_flash_impr6`) | +10.8 |
| 9 | 50 | 84.0% (`v2_flash_orig9`) | 92.0% (`v2_flash_impr9`) | +8.0 |
| 12 | 63 | 77.8% (`v2_flash_orig`) | 92.1% (`v2_flash_impr`) | +14.3 |

The original descriptions lose 22.2pt going from 3 to 12 tools; the rewritten
ones lose 7.9pt. The gap does not close with scale — it widens, because more
siblings means more chances to pick the wrong one.

## Other suites

- `result.json` — one full `bench.py all` run: `selection`, `latency`, `e2e`,
  `net` (cache speedup) and `injection` (red team) in one payload.
- `result_matrix.json` — index over the ten selection runs above, so the
  curve can be reconstructed without opening each file.

## Reproduce

```bash
export OPENAI_API_KEY=...          # DeepSeek key, OpenAI-compatible endpoint
python bench.py select --limit 12 --describe original --out /tmp/a.json
python bench.py select --limit 12 --describe improved --out /tmp/b.json
```

Expect variance run to run. The improvement is large enough to survive it;
the exact decimal places are not a spec.
