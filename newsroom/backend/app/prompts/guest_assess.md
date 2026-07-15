# 嘉宾单轮判定：第一阶段

你只负责先判定主持人这一句的 `pressure` 和 `targeted_fact`，不要生成嘉宾回答。

## pressure 固定评分标准

- 0-1：寒暄、共情或开放式泛问。例如：“您当时是什么感受？”
- 2：开放式但有方向。例如：“能说说 3 月那阵子的情况吗？”
- 3：具体事实追问，但没有摆出矛盾或证据。例如：“3 月 12 日你们开会了吗？”
- 4：出示矛盾、证据或引用第三方。例如：“但你同事说会开了。”
- 5：直球指控并且不给退路。例如：“所以你是在撒谎？”

只按主持人这一句实际使用的问法评分，不能因为某条 fact 很严重就提高 pressure。

## targeted_fact 判定

- 从 dossier 的 facts 中选择这一句最直接追问的一条，输出它的 id。
- 只看主题、时间、人物、数字、组织和 `unlock_hint` 是否匹配；不能按最高 juiciness 猜测。
- 同时碰到多条时，选主持人要求嘉宾直接确认或解释的那条。
- 只是寒暄、泛谈公开履历或没有命中任何隐藏 fact 时，输出 null。
- 已经 partial/full 的 fact 仍可成为 targeted_fact。

## 输入

### dossier

{dossier_json}

### 全部 fact_state

{fact_states_json}

### 对话历史

{history_json}

### 主持人这一句

{host_message_json}

严格按结构化输出要求作答，并保持字段顺序：先 `pressure`，再 `targeted_fact`。
