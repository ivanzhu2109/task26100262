# task26100262

## jvmstack：JVM 折叠堆栈分析库

纯进程内内存库（无接口层），供缺陷详情页上层逻辑调用。输入是按出现顺序
排列的异常记录（已解析好多层 `Caused by` / suppressed 与 `... N more`），
装载后提供只读查询：调用深度、起始帧、任意偏移的连续帧片段。

### 数据模型

```python
from jvmstack import ExceptionRecord, Relation, StackReport

ExceptionRecord(
    exc_id="ex-1",            # 唯一异常编号
    parent_id="ex-0",         # 直接父异常编号；根异常为 None
    relation=Relation.CAUSE,  # CAUSE 或 SUPPRESSED；根异常为 None
    frames=("f0", "f1"),      # 显式帧，栈顶 -> 栈底；允许为空
    omitted=2,                # "... N more" 的 N；根异常为 0
)
```

异常的完整调用栈 = 显式帧 + 直接父异常**完整栈**的最后 `N` 帧。

### API

```python
report = StackReport()
report.load(records)          # 按出现顺序装载；之后只读，重复装载抛 RuntimeError
report.depth(exc_id)          # 完整调用深度（含继承帧）
report.first_frame(exc_id)    # 起始帧（栈顶）；空栈返回 None
report.read_frames(exc_id, offset, length)  # 零基偏移连续片段，越界自动截断
```

### 实现要点

- 深度即 `len(显式帧) + N`，O(1) 查询。
- 帧定位用倍增跳跃表（`up/add/mx` 三张 `array('q')` 表）：把"位置落在哪个
  异常的哪条显式帧上"的解析降到 O(log n)，装载时 O(n log n) 构建。
- 每个异常预计算"显式帧耗尽后的续接位置"，片段读取跨异常时 O(1) 跳转，
  单次读取复杂度 O(log n + 片段跨越的异常数)。
- 无递归，任意深度的因果链不会栈溢出；逻辑展开总量可超过 10 亿帧。

### 测试

```bash
python3 -m pytest tests/ -q -s
```

- `tests/test_small.py`：小规模报告与独立逐层展开（`tests/naive.py`）全量
  对照，覆盖空显式帧、重复帧、连续多层部分尾帧继承、随机 fuzz。
- `tests/test_siblings.py`：一个父异常的多个 suppressed 子异常各自带
  cause，验证兄弟间不误用尾帧。
- `tests/test_scale.py`：可复现规模测试（10 万异常 / 20 万显式帧 / 5 万层
  深链 / suppressed 分叉 / 部分尾帧继承，逻辑展开约 12.5 亿帧），记录装载
  耗时、2 万次分散片段读取耗时与峰值 RSS，并逐帧核对内容。
