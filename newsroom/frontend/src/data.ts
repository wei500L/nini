import type { ReviewData } from "./types";

export const surfaceBio =
  "周启明是新任食堂承包商惠食餐饮的创始人兼 CEO。校方公开称，本次更换经过公开招标，原因是原承包商连续两次食品安全抽检不合格。惠食餐饮承诺保持基础餐价格稳定，并在三个月内完成后厨升级。";

export const demoReview: ReviewData = {
  id: "NX-0715-042",
  topic: "某高校食堂承包商更换风波",
  personaName: "满嘴套话的企业家",
  total: 72,
  duration: "07:36",
  dimensions: [
    { name: "问题设计", score: 19, max: 25 },
    { name: "倾听追问", score: 20, max: 30 },
    { name: "现场控制", score: 15, max: 20 },
    { name: "语言表达", score: 11, max: 15 },
    { name: "信息收获", score: 7, max: 10 },
  ],
  advice: [
    "抓住嘉宾对首次进校日期的改口，连续追问具体日期与人员。",
    "每轮只保留一个问题，减少背景复述，给嘉宾留下明确答题空间。",
    "当导播提示时间线有缺口时，先钉死顺序，再切换到关系线。",
  ],
  rounds: [
    {
      round: 1,
      timestamp: "00:42",
      host: "周总，惠食最早是什么时候知道学校要更换承包商的？",
      guest:
        "我们一直关注高校餐饮行业的公开机会，所有信息都是以学校正式公告为准。",
      stageDirection: "身体前倾，语速平稳",
      director: "别接套话，问首次进校日期",
      studentAction: "追问了第一次进入学校后厨的具体时间。",
      followed: true,
    },
    {
      round: 2,
      timestamp: "01:28",
      host: "那你们第一次派人进学校后厨，具体是哪一天？",
      guest:
        "这个日期我确实记不太清了。团队做过一些常规的前期了解，但这和投标结果没有关系。",
      stageDirection: "停顿，低头翻看日程",
      director: "他在日期上松了，追谁去的",
      studentAction: "转而询问招标流程是否公开，没有追问进校人员。",
      followed: false,
    },
    {
      round: 3,
      timestamp: "02:16",
      host: "学校说流程完全公开，你能确认评审和贵公司此前没有合作吗？",
      guest:
        "评审团队是独立评审、独立评审。我们尊重每一位专家的专业判断。",
      stageDirection: "连续重复“独立评审”",
      director: "重复就是破绽，点林志远",
      studentAction: "点出林志远的名字，并追问过往咨询合作。",
      followed: true,
    },
    {
      round: 4,
      timestamp: "04:03",
      host: "林志远连续三年为惠食做品牌咨询，这段关系申报了吗？",
      guest:
        "林老师服务过很多企业。具体申报由合规团队负责，我不能替团队确认每一份表格。",
      stageDirection: "双手交叠，避开视线",
      director: "不要让他甩给团队，要是或不是",
      studentAction: "继续追问他本人是否知情，但问题中加入了较长背景说明。",
      followed: true,
    },
    {
      round: 5,
      timestamp: "05:47",
      host: "再谈物流，启顺供应链是怎么进入你们供应商名单的？",
      guest:
        "启顺通过了全部资质审核，我们选择合作伙伴只看专业能力和服务质量。",
      stageDirection: "回答突然变慢",
      director: "变慢了，追决策人和回避机制",
      studentAction: "询问了启顺的资质标准，未追问决策人或亲属关系。",
      followed: false,
    },
  ],
  dossier: [
    {
      id: "F1",
      content:
        "惠食餐饮在中标前一个月就派团队测量过食堂后厨，内部排班表也提前预留了进场日期。",
      juiciness: 1,
      status: "found",
      unlockHint: "追问中标前为何已经进校测量，以及测量人员和具体日期。",
    },
    {
      id: "F2",
      content:
        "招标评分顾问林志远此前连续三年为惠食餐饮提供品牌咨询，但未在评审利益关系表中披露。",
      juiciness: 2,
      status: "found",
      unlockHint: "拿顾问姓名和合作年份核对，要求说明是否主动申报利益关系。",
    },
    {
      id: "F3",
      content:
        "原承包商在第二次食品安全抽检前就收到过未署日期的退场方案，校方公开解释的时间线并不完整。",
      juiciness: 3,
      status: "missed",
      unlockHint: "按时间顺序钉住退场方案、第二次抽检和招标公告日期。",
    },
    {
      id: "F4",
      content:
        "惠食把食材物流分包给启顺供应链，而启顺 30% 的股份由周启明的妹夫代持。",
      juiciness: 4,
      status: "missed",
      unlockHint: "把供应链股权记录与家庭关系并置，追问分包决策是否回避。",
    },
    {
      id: "F5",
      content:
        "惠食与启顺另有未披露的返款协议：每月物流结算额的 6% 以品牌管理费名义回到周启明控制的公司。",
      juiciness: 5,
      status: "missed",
      unlockHint: "引用协议中的比例、费用名目和收款公司，要求解释资金用途。",
    },
  ],
  metrics: [
    { name: "开放式问题比例", value: "43%", ideal: "≥ 60%", inRange: false },
    { name: "有效追问率", value: "40%", ideal: "≥ 50%", inRange: false },
    { name: "主持人话语占比", value: "31%", ideal: "20–35%", inRange: true },
    { name: "平均问题长度", value: "24 字", ideal: "≤ 28 字", inRange: true },
    { name: "多问合一", value: "2 次", ideal: "0 次", inRange: false },
    { name: "口头禅「就是」", value: "0.4/句", ideal: "≤ 0.2/句", inRange: false },
  ],
};
