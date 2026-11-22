---
name: Bug 报告
about: 仪表盘显示错误、崩溃、或数据不准
title: '[Bug] '
labels: bug
assignees: ''
---

## 环境

```
系统:      （如 Ubuntu 24.04 / kernel 6.8.0）
Python:    （python3 --version）
终端:      （echo $TERM; stty size）
是否 root: （是/否）
```

## 复现步骤

1.
2.
3.

## 预期行为

（应该显示什么）

## 实际行为

（实际显示了什么，贴截图或文字）

## 诊断信息

请附上这条命令的输出：

```bash
python3 serverdash.py --json | python3 -m json.tool | head -80
```

## 如果是传感器读不到

先确认环境本身有没有这个传感器：

```bash
# hwmon 芯片清单
ls /sys/class/hwmon/*/name | xargs -I{} sh -c 'echo "{}: $(cat {})"'

# 温度点
ls /sys/class/hwmon/hwmon*/temp*_input 2>/dev/null

# thermal zone
for z in /sys/class/thermal/thermal_zone*; do
  echo "$(basename $z): $(cat $z/type 2>/dev/null) = $(cat $z/temp 2>/dev/null)"
done
```

**如果上面命令也没输出，说明是系统/驱动层面没有，不是本工具的问题。**
