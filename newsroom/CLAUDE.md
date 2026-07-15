## 术语表（代码里必须用这些名字，不许自己改名）
- fact / 料：嘉宾的隐藏事实
- guard / 防线：这条料的解锁难度 0-5
- pressure / 压迫度：主持人问题的攻击力 0-5
- reveal / 松口、tell / 破绽、deflect / 打太极
- dossier / 档案、host / 主持人（学生）

## 铁律
1. 客观指标（开放式比例、追问率、话语占比、口头禅）一律用纯代码算，禁止调 LLM。这是本项目的设计核心。
2. 导播的提示里绝不允许出现 fact 的具体内容，只能给方向（"他刚在时间上顿了一下"）。
3. 所有 agent 的输出必须过 Pydantic schema 校验，校验失败最多重试 2 次，再失败降级到兜底值，绝不抛给用户。
4. 每次 LLM 调用必须落盘到 docs/llm-calls/{date}/{trace_id}.json，含 prompt/response/耗时/token。这是结课报告的证据。
5. 提示词全部放在 app/prompts/*.md，代码只负责 load + format，禁止把 prompt 字符串写进 .py。
6. 改任何 prompt，必须同步在 docs/prompt-changelog.md 追加一条：版本 / 改了啥 / 为什么改 / 改完效果。

## 不要做
- 不要引入任何 agent 框架，编排器我要自己手写状态机（因为报告里要画流程图）
- 不要加用户系统、登录、部署配置
- 不要为了"健壮"加一堆 try/except 吞掉错误
