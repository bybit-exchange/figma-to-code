# P1.5 内联脚本参考

## CHUNK_SPLIT — 分批脚本

把所有需要重命名的 `frame-*/node-*` 条目按 50 条一批写成独立 chunk 文件：

```bash
python3 - << 'CHUNK_SPLIT'
import json, math, pathlib, glob

BATCH = 50
naming_files = glob.glob('.figma-to-code/3-page-code/*/split-a/plan.naming.json')
for f in naming_files:
    naming = json.load(open(f))
    entries = naming.get('cssClasses', [])
    # 去重：同一 currentClass 取 firstTexts 最丰富的一条
    seen = {}
    for e in entries:
        k = e['currentClass']
        if k not in seen or len(e.get('firstTexts', [])) > len(seen[k].get('firstTexts', [])):
            seen[k] = e
    unique = [v for k, v in seen.items() if k.startswith(('frame-', 'node-'))]

    total = math.ceil(len(unique) / BATCH)
    print(f"\n📦  {pathlib.Path(f).parts[-3]}: {len(unique)} 条需要重命名，共 {total} 批")
    base = pathlib.Path(f).parent
    for i in range(total):
        chunk = unique[i * BATCH:(i + 1) * BATCH]
        p = base / f'naming-chunk-{i+1}-of-{total}.json'
        json.dump(chunk, open(p, 'w'), ensure_ascii=False, indent=2)
        print(f"  批次 {i+1}/{total} ({len(chunk)} 条) → {p}")
CHUNK_SPLIT
```

## MERGE_BATCH — 合并单批结果

每批推断完后，将该批的映射赋值给环境变量 `BATCH_MAP` 再运行：

```bash
python3 - << 'MERGE_BATCH'
import json, pathlib, glob, os

base = pathlib.Path(glob.glob('.figma-to-code/3-page-code/*/split-a/plan.naming.json')[0]).parent
renames_path = base / 'renames.json'
renames = json.load(open(renames_path)) if renames_path.exists() else {}
renames.setdefault('cssClasses', {})

batch_map = json.loads(os.environ['BATCH_MAP'])   # 当前批次推断结果
renames['cssClasses'].update(batch_map)
json.dump(renames, open(renames_path, 'w'), ensure_ascii=False, indent=2)
print(f"✓  已合并 {len(batch_map)} 条，cssClasses 累计 {len(renames['cssClasses'])} 条")
MERGE_BATCH
```

## COVERAGE_CHECK — 覆盖率验证

退出码非 0 → 必须补全 `renames.json` 再重跑，禁止跳过：

```bash
python3 - << 'COVERAGE_CHECK'
import json, sys, pathlib, glob

naming_files = glob.glob('.figma-to-code/3-page-code/*/split-a/plan.naming.json')
if not naming_files:
    print("⚠️  找不到 plan.naming.json，跳过检查")
    sys.exit(0)

all_ok = True
for f in naming_files:
    naming = json.load(open(f))
    renames_path = pathlib.Path(f).parent / 'renames.json'
    renames = json.load(open(renames_path)) if renames_path.exists() else {}

    needed = {e['currentClass'] for e in naming.get('cssClasses', [])
              if e['currentClass'].startswith(('frame-', 'node-'))}
    mapped = set(renames.get('cssClasses', {}).keys())
    missing = needed - mapped

    if missing:
        print(f"⛔  仍有 {len(missing)}/{len(needed)} 个 frame-*/node-* 类名未映射，必须补全后重跑：")
        for m in sorted(missing):
            print(f"    {m}")
        all_ok = False
    else:
        label = pathlib.Path(f).parts[-3]
        print(f"✓  {label}: 全部 {len(needed)} 条 frame-*/node-* 已映射")

sys.exit(0 if all_ok else 1)
COVERAGE_CHECK
```
