# jvmstack — JVM 折叠堆栈分析库

解析开发者上报的 JVM 异常报告（多层 `Caused by` / suppressed，`... N more`
省略与父异常相同的尾部帧），在进程内内存中建立只读索引，支持：

- `depth(exception_id)`：完整调用深度（含继承帧）
- `start_frame(exception_id)`：完整栈起始帧（栈顶），空栈返回 `None`
- `read_frames(exception_id, offset, length)`：按零基偏移读取连续帧片段，
  结果与真正展开后的序列切片一致（含截到栈尾、偏移超界、空栈）

无接口层、无外部服务，供缺陷详情页的上层逻辑直接调用。

## 用法

```python
from jvmstack import ExceptionRecord, Relation, StackReport

records = [
    ExceptionRecord("e0", None, None, ["a", "b", "c"], 0),
    ExceptionRecord("e1", "e0", Relation.CAUSE, ["x"], 2),      # ... 2 more
    ExceptionRecord("e2", "e0", Relation.SUPPRESSED, ["y"], 1), # suppressed
]
report = StackReport.load(records)   # 装载后只读

report.depth("e1")              # 3: [x, b, c]
report.start_frame("e1")        # "x"
report.read_frames("e1", 1, 2)  # ["b", "c"]
```

输入约定：记录按出现顺序提交，父异常先于子异常；根异常 `parent_id=None`、
`relation=None`、`omitted=0`；`omitted` 不超过父异常完整深度。每帧为不透明
文本，内容相同的帧仍各占位置。异常 `e` 的完整栈 = 显式帧依原顺序拼接
**直接父异常完整栈**的最后 `omitted` 帧。

## 实现要点

- `depth(e) = len(frames_e) + omitted_e`，装载时 O(1) 求得。
- 帧定位视为“向上爬祖先链 + 下标位移”，用倍增表（binary lifting，
  `up/need/summ` 三张表）将单帧来源解析降到 O(log E)。
- 每个异常预计算继承段首帧的来源指针 `next_run`，片段读取只需对起始
  偏移做一次倍增解析，之后跨异常边界均 O(1) 跳转。
- 全程无递归，任意深度的因果链（实测 5 万层）不会栈溢出。
- 索引使用 `array('q')` 紧凑存储，10 万异常规模下索引约 40 MiB。

## 测试

```bash
python3 -m pytest                    # 全部（含规模测试）
python3 -m pytest -m "not scale"     # 仅快速正确性测试
python3 -m pytest tests/test_report_scale.py -q -s   # 复现规模数据
```

小规模测试将库结果与独立逐层展开的参照实现全量对比（空显式帧、重复帧、
连续多层部分尾帧继承、suppressed 兄弟互不串栈等）。

规模测试（10 万异常 / 20 万显式帧 / 5 万层深链 / suppressed 分叉 /
逻辑展开约 34 亿帧）本机实测：

| 指标 | 实测 | 预算 |
| --- | --- | --- |
| 装载耗时 | 0.60 s | ≤ 10 s |
| 2 万次分散片段读取 | 0.16 s | ≤ 5 s |
| 峰值常驻内存 | 198.5 MiB（含 pytest 与参照结构；库本身约 75 MiB） | ≤ 384 MiB |
