## 这个 PR 做了什么

<!-- 一句话说清，例：实现测点改名接口，改名后同步返回影响面摘要 -->

关联 issue：`G05-__`

## 我负责的角色

- [ ] 我只改了自己 `dist/roles/<角色>/` 里的文件（`git diff --name-only` 自查过）

## 我做了哪些验证

- [ ] `python -m g05 preflight` → `passed: true`
- [ ] `python -m pytest -q` → 全绿，用例数 __
- [ ] 手工验证（写出具体操作步骤与结果）

## 有没有改契约

- [ ] 没有动 `src/g05/schemas/dto.py` / `config/*.yaml` / 数据库表结构
- [ ] 动了，已在 issue 里与 A 确认（issue 链接：__）

## 截图 / 数据

<!-- 性能类改动必须附前后对比数据；UI 改动必须附截图 -->
