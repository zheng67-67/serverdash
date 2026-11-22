## 贡献指南

欢迎提 Issue 和 PR。这个项目刻意保持简单，所以有几点先说明。

### 项目边界（我不打算扩展的方向）

这个工具**只做一件事**：登录时/随时看本机状态。

以下功能明确不做，提 PR 也会被拒：

| 不做 | 原因 |
|---|---|
| 多机聚合 | 那是 netdata 的领域 |
| 历史数据/曲线图 | 需要存储层，违背"单文件零依赖" |
| Web UI | 违背"不联网、不监听端口" |
| 告警推送 | 同上 |
| 进程列表 / 杀进程 | 那是 htop 的领域 |
| 修改任何系统配置 | 违背"只读"原则 |

**如果你的需求在这张表里，看看 [README 的"类似工具"章节](README.md#类似工具)。**

### 可以接受的方向

- 🐛 **Bug 修复** —— 采集错误、渲染错位、崩溃
- 🖥️ **新硬件支持** —— 新的 hwmon 芯片、特殊的 `/sys` 路径
- 🌐 **国际化** —— 目前只有中文/英文回退，加其他语言欢迎
- ⚡ **性能优化** —— 更少的 syscall、更低的 CPU
- 📖 **文档改进** —— 错别字、说明不清、缺示例

### 提交 PR 前

```bash
# 1. 语法检查
python3 -m py_compile serverdash.py
bash -n install.sh
bash -n uninstall.sh
bash -n get.sh

# 2. 冒烟测试
python3 serverdash.py --version
python3 serverdash.py --banner --no-color
python3 serverdash.py --json > /dev/null

# 3. 确认没泄露隐私
grep -rniE '192\.168\.|10\.[0-9]+\.|/home/[a-z]+/' --include='*.py' --include='*.sh' .
```

### 代码风格

- **Python**：PEP 8，但不用强制工具检查。行宽 100。
- **注释**：每个采集函数必须在 docstring 里写明数据来源的 sysfs 路径。
- **中文文案**：所有面向用户的字符串必须走 `T` 字典，不能硬编码（否则 `LANG=C` 会乱码）。
- **错误处理**：读不到的项返回 `None`，渲染层显示 `N/A` 或 `需 root`，**不能抛异常**。

### 新增数据源的规范

如果你要加一个新指标，PR 里必须包含：

1. **数据来源** —— 代码注释里写清 `/sys/...` 或 `/proc/...` 路径
2. **降级行为** —— 读不到时显示什么（`N/A` / `需 root` / 整块隐藏）
3. **区分「读不到」和「真的是 0」** —— 这是这类工具最常见的坑
   ```python
   # ❌ 错：0 和读不到混在一起
   value = read_int(path) or 0

   # ✅ 对：None 表示读不到，0 表示真的是 0
   value = read_int(path)
   if value is None:
       display = "N/A"
   else:
       display = f"{value}"
   ```

### Issue 模板

提 Bug 时请包含：

```
系统:      Ubuntu 24.04 / kernel 6.x
Python:    3.12.3
终端:      TERM=xterm-256color, 150x45
复现步骤:  ...
预期:      ...
实际:      ...
截图/日志: （有的话最好）
```

**如果是传感器读不到**，先跑这两条确认是不是环境问题：

```bash
ls /sys/class/hwmon/*/name          # 看有哪些芯片
cd ~/serverdash && python3 serverdash.py --json | python3 -m json.tool | head -50
```

### 许可证

提交 PR 即表示你同意以 MIT 许可证发布你的贡献。
