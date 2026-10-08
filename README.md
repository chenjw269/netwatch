# NetWatch

桌面悬浮窗小组件，用于网络波动检测和诊断，并辅助定位网络问题出现在哪一段链路（电脑→路由器→互联网）。平时基本不占用资源，固定一段时间检测一次网络质量。

![展开详情，看到诊断结果和链路测试，再收起](docs/usage.gif)

## 安装

[下载 NetWatch-Setup.exe](https://github.com/chenjw269/netwatch/releases/latest/download/NetWatch-Setup.exe)

双击安装，不用管理员权限。装完自己打开，停在屏幕右下角，开机也会启动。

窗口不在任务栏里。要退出，展开后点「退出」，或在窗口上右键选「退出」。

## 怎么看

左边是这一轮的结论，右边是到互联网的延迟。下面两行分别是到路由器、再到互联网。最后一行时间是上一轮测完的时刻。

单击概况可以展开，按住可以拖动。展开后底部改监测间隔。右键里可以立刻重测、暂停、开关机启动，也可以做丢包率测试、复制诊断结果。

到互联网测的是 `223.5.5.5`，不走系统代理。结论的规则在 `netwatch/diagnose.py`。

## 从源码运行

Windows，Python 3.9 或更高，需要带 tkinter。

```powershell
python netwatch.pyw
```

再运行一次会直接退出，避免开出两个窗口。

## 打包

需要 [Inno Setup 6](https://jrsoftware.org/isinfo.php)。在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

生成 `dist\NetWatch-Setup.exe`。安装包挂到 GitHub Release，不要把 `dist` 提交进 git。
