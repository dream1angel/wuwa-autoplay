# 截图目录

这里放 README 引用的 4 张界面截图，全部由脚本自动生成，**不需要手动截屏**：

```powershell
python tools/make_screenshots.py            # 输出到本目录
python tools/make_screenshots.py --show     # 同时在屏幕上显示（调样式时用）
```

| 文件名 | 内容 |
|---|---|
| `main.png` | 主界面全貌（工具栏、运输条、节拍网格、虚拟键盘、状态栏），自然阶载入《小星星》 |
| `chromatic.png` | 半音阶模式：3×12 虚拟键盘 + 格内 `#1`/带八度点的符号 |
| `settings.png` | 设置 →「半音阶」页（36 键映射编辑器、升半音修饰键、黑键策略） |
| `export.png` | 导出的 PNG 曲谱效果 |

原理：脚本给窗口打开 `Qt.WA_DontShowOnScreen`，控件照常完成布局与绘制但不会真的出现在桌面上，
再用 `QWidget.grab()` 把客户区渲染成图片。用的是真实的 Windows 平台插件，字体与控件风格跟平时
一致（自检用的 `offscreen` 平台没有字体，截出来会是方框）。因此脚本可以随时重跑，也不会打断
你手头的操作。

注意：

* 脚本只在截图期间把窗口标记为「不显示」，不需要管理员权限，也不会占用全局快捷键；
* 设置写到一个临时文件，不会覆盖你正在用的 `settings.json`；
* 改了界面配色或布局（`ui/theme.py`、`ui/grid_widget.py` 等）之后重跑一次即可更新截图。
