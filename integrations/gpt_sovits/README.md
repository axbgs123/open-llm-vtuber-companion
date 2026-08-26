# GPT-SoVITS 本地声音克隆

这里封装的是官方 [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)。
Open-LLM-VTuber 通过它的本地 `/tts` API 合成克隆声音，服务只监听
`127.0.0.1:9880`。

1. 首次双击 `install.command`。它会在本目录安装隔离的 Miniforge、GPT-SoVITS
   和官方预训练模型，不修改 Open-LLM-VTuber 的 Python 环境。
2. 以后先双击 `start.command`，保持终端窗口开启。
3. 打开 `http://127.0.0.1:12393/companion/`，在“声音克隆”上传 3–10 秒
   干净人声，填写录音原文，试听后按角色启用。
4. 启用声音档案后重启 Open-LLM-VTuber。

只克隆你本人或明确授权给你的声音。不要冒充他人，也不要将克隆声音用于欺骗、
骚扰或绕过身份验证。GPT-SoVITS 及模型的许可证以其上游仓库为准。
