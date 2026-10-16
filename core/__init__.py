# -*- coding: utf-8 -*-
"""核心逻辑层（与 GUI 完全解耦）。

本包内**不允许** import PySide6 或任何 GUI 库，以便：
1. 演奏引擎可以脱离界面单独跑基准测试（见 tests/）；
2. 后续若换成其它界面框架（甚至纯命令行）也不需要改核心代码。

模块划分
--------
notes     音区 / 音级 ↔ 物理按键 的映射表与校验
model     曲谱数据模型（Score / ScoreMeta / ScoreNote）+ 合法性校验
timeline  编辑器使用的槽位↔小节换算、音符增删改、区域复制粘贴
undo      快照式撤销/重做栈
clock     高精度混合计时（perf_counter + 粗睡 + 忙等）
backends  按键后端抽象（pydirectinput / interception / dummy）
engine    演奏引擎：事件展开 + 状态机 + 调度线程
focus     前台窗口检测（失焦自动暂停用）
hotkeys   全局热键注册
config    设置持久化（便携模式 settings.json）
"""
