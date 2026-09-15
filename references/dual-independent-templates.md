# 双稿独立模式 — 模板与落地参考

## 入口 index.tsx 模板

生成 `$PAGE_DIR/index.tsx`：

```tsx
import { usePageEnv } from './hooks/usePageEnv';
import PcPage from './pc';
import H5Page from './h5';

export default function PageName() {
  const { isMobile } = usePageEnv();
  return isMobile ? <H5Page /> : <PcPage />;
}
```

> 注意：外层入口 `usePageEnv()` **不传 theme 参数**。各端 `pc/index.tsx` 和 `h5/index.tsx` 各自通过自己的 `usePageEnv('light'|'dark')` 管理各自的 theme。

## hooks/usePageEnv.ts 模板

生成 `$PAGE_DIR/hooks/usePageEnv.ts`（若不存在）：

```ts
import { useState, useEffect } from 'react';

const MOBILE_BREAKPOINT = 768;

export function usePageEnv(theme?: 'light' | 'dark') {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= MOBILE_BREAKPOINT
  );

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  useEffect(() => {
    if (theme) {
      const prev = document.documentElement.getAttribute('data-theme');
      document.documentElement.setAttribute('data-theme', theme);
      return () => {
        if (prev) document.documentElement.setAttribute('data-theme', prev);
        else document.documentElement.removeAttribute('data-theme');
      };
    }
  }, [theme]);

  return { isMobile };
}
```

## .figma-source.json 写入

### 单稿模式

```python
import json, pathlib, datetime

meta_path = pathlib.Path(f"$TARGET_DIR/$DEST_DIR/{COMPONENT_NAME}/.figma-source.json")
meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
meta["single"] = {
    "url": "$FIGMA_URL",
    "nodeId": "$NODE_ID_SAFE",
    "fileKey": "$FILE_KEY",
    "convertedAt": datetime.datetime.utcnow().isoformat() + "Z"
}
meta_path.write_text(json.dumps(meta, indent=2))
```

### 双稿合并模式（--merge）

```python
import json, pathlib, datetime

meta_path = pathlib.Path(f"$TARGET_DIR/$DEST_DIR/{COMPONENT_NAME}/.figma-source.json")
meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"pc": None, "h5": None}
meta["pc"] = {
    "url": "$PC_URL", "nodeId": "$PC_NODE_ID_SAFE",
    "fileKey": "$PC_FILE_KEY",
    "convertedAt": datetime.datetime.utcnow().isoformat() + "Z"
}
meta["h5"] = {
    "url": "$H5_URL", "nodeId": "$H5_NODE_ID_SAFE",
    "fileKey": "$H5_FILE_KEY",
    "convertedAt": datetime.datetime.utcnow().isoformat() + "Z"
}
meta_path.write_text(json.dumps(meta, indent=2))
```

### 双稿独立模式（P1.4-Ind）

```python
import json, pathlib, datetime

meta_path = pathlib.Path(f"$TARGET_DIR/$DEST_DIR/{PAGE_NAME}/.figma-source.json")
meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
meta["mode"] = "independent"
meta["pc"] = {
    "url": "$PC_URL", "nodeId": "$PC_NODE_ID_SAFE",
    "fileKey": "$PC_FILE_KEY",
    "convertedAt": datetime.datetime.utcnow().isoformat() + "Z"
}
meta["h5"] = {
    "url": "$H5_URL", "nodeId": "$H5_NODE_ID_SAFE",
    "fileKey": "$H5_FILE_KEY",
    "convertedAt": datetime.datetime.utcnow().isoformat() + "Z"
}
meta_path.write_text(json.dumps(meta, indent=2))
```

> ⚠️ 公开仓库建议将 `.figma-source.json` 加入 `.gitignore`。
