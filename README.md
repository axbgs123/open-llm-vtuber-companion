# Open-LLM-VTuber Companion

一个面向中文对话、本地优先的沉浸式 AI 伴侣项目。

本项目在 Open-LLM-VTuber 的语音对话基础上，集中开发角色连续性、3D 数字人和中文声音克隆能力。目前由个人与朋友共同维护，欢迎提交 Issue 和 Pull Request。

## 我们增加的功能

### 长期记忆与历史检索

- 为每个角色保存独立的核心记忆和结构化记忆；
- 使用 SQLite FTS5 进行本地全文检索；
- 可选用 `qwen3-embedding:0.6b` 做语义检索；
- 记录事实来源、置信度、有效期和确认状态；
- 检测互相冲突的用户信息，并交由用户确认；
- 保存关系连续性、近期话题和待办承诺。

### 角色管理

- 创建、编辑、切换和归档角色；
- 每个角色分别保存人设、记忆、声音、VRM 形象和动作设置；
- 删除等高风险操作前自动创建加密备份；
- 支持备份预览、恢复密钥和跨设备恢复。

### 主动陪伴

- 沉默一段时间后，根据最近对话和待办事项主动发起话题；
- 支持安静时段、每日次数限制和近期话题去重；
- 用户未回应时自动降低主动频率；
- 在会议、全屏、锁屏或用户离开时抑制主动发言。

### VRM 3D 数字人

- 支持导入 VRM 0.x/1.0 模型，也可回退到 Live2D；
- 情绪表情、注视、实时口型和自然待机；
- 根据回复语义触发挥手、点头、摇头、思考、害羞、惊讶等动作；
- 区分高兴、狂喜、愤怒、疲惫、恐惧等不同强度和动作组合；
- 支持导入 VRMA、组合多个动作并绑定语义触发条件；
- 提供 IK 朝向、动作混合和身体碰撞修正，减少穿模；
- 角色可在场景中自由拖动，并按角色保存位置。

### 中文声音克隆

- 封装 CosyVoice3 0.5B 为仅监听本机的声音服务；
- 每个角色可保存多个音色档案和参考音频；
- 统一驱动语气、表情与身体动作；
- 支持语速、情绪强度、分段流式生成和音频缓存；
- 按需加载并在空闲后释放模型，降低常驻内存占用。

### 本地数据与诊断

- 聊天、记忆、声音、VRM、模型权重和密钥默认仅保存在本机；
- 诊断中心只记录耗时、成功状态、缓存命中和内存峰值，不保存对话正文；
- 服务退出时可自动卸载 Ollama、语义检索和 CosyVoice 模型。

完整设计与实现细节见 [COMPANION_FEATURES.md](./COMPANION_FEATURES.md)。

## 快速运行

当前一键启动和 CosyVoice3 安装流程主要针对 macOS；Windows 与 Linux 的启动脚本仍待完善。

需要先安装 [Git](https://git-scm.com/)、[uv](https://docs.astral.sh/uv/) 和 [Ollama](https://ollama.com/)：

```bash
git clone --recurse-submodules https://github.com/axbgs123/open-llm-vtuber-companion.git
cd open-llm-vtuber-companion
cp config_templates/conf.ZH.default.yaml conf.yaml
uv sync
ollama pull qwen2.5:3b
```

根据实际模型修改 `conf.yaml` 后，macOS 可双击 `start-companion.command`，或运行：

```bash
./start-companion.command
```

管理界面：`http://127.0.0.1:12393/companion/`

可选安装 CosyVoice3：

```bash
./integrations/cosyvoice/install.command
```

CosyVoice3 的隔离环境和模型约占 10 GB 磁盘；完整本地对话、VRM、语义检索与声音克隆同时运行时，建议预留 12–18 GB 内存。

## 开发验证

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check src/open_llm_vtuber tests
git diff --check
```

## 数据与版权

仓库不包含维护者本地使用的 VRM、克隆声音、模型权重、聊天记录、记忆数据或 API 密钥。请只上传你有权公开的角色、声音、动作和媒体资源。

本项目基于 [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) 二次开发。不同组件适用不同许可证，使用或分发前请阅读 [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)、[LICENSE](./LICENSE)、[LICENSE-Frontend.md](./LICENSE-Frontend.md) 和 [LICENSE-Live2D.md](./LICENSE-Live2D.md)。
