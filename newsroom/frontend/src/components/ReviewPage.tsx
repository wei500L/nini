import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Check,
  ChevronDown,
  CircleGauge,
  FileWarning,
  Headphones,
  Lightbulb,
  Minus,
  Target,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

import type { DimensionScore, ReviewData } from "../types";

function ScoreTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: DimensionScore & { normalized: number } }>;
}) {
  if (!active || !payload?.[0]) return null;
  const item = payload[0].payload;
  return (
    <div className="radar-tooltip">
      <strong>{item.name}</strong>
      <span>{item.score} / {item.max} 分</span>
    </div>
  );
}

function ScoreRadar({ dimensions }: { dimensions: DimensionScore[] }) {
  const data = dimensions.map((dimension) => ({
    ...dimension,
    normalized: Math.round((dimension.score / dimension.max) * 100),
  }));

  return (
    <div className="radar-wrap">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} outerRadius="67%">
          <PolarGrid stroke="#2b3039" radialLines />
          <PolarRadiusAxis
            type="number"
            domain={[0, 100]}
            allowDataOverflow
            tick={false}
            axisLine={false}
          />
          <PolarAngleAxis
            dataKey="name"
            tick={{ fill: "#a8adb7", fontSize: 12 }}
          />
          <Radar
            dataKey="normalized"
            stroke="#e14b3f"
            fill="#e14b3f"
            fillOpacity={0.2}
            strokeWidth={2}
            dot={{ r: 3, fill: "#0f1115", stroke: "#f4776e", strokeWidth: 2 }}
          />
          <Tooltip content={<ScoreTooltip />} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ReviewPage({ reviewId }: { reviewId: string }) {
  const [report, setReport] = useState<ReviewData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/review/${reviewId}`)
      .then((response) => {
        if (!response.ok) throw new Error("真实复盘不存在或尚未生成完成。");
        return response.json() as Promise<ReviewData>;
      })
      .then((data) => {
        if (!cancelled) setReport(data);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "复盘加载失败。");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reviewId]);

  if (error) {
    return (
      <main className="route-state">
        <FileWarning size={28} />
        <h1>复盘加载失败</h1>
        <p>{error}</p>
        <a href="/">返回首页</a>
      </main>
    );
  }
  if (!report) {
    return (
      <main className="route-state">
        <Activity className="spin" size={28} />
        <h1>正在读取真实复盘</h1>
      </main>
    );
  }

  const missedRounds = report.rounds.filter(
    (round) => round.director && round.followed === false,
  ).length;
  const foundFacts = report.dossier.filter((fact) => fact.status === "found").length;
  const comparison = report.comparison ?? [];

  return (
    <main className="review-shell">
      <header className="review-topbar">
        <a href="/" className="back-link"><ArrowLeft size={16} /> 返回演播室</a>
        <div className="review-brand">
          <span className="brand-mark">N</span>
          <span><strong>NEWSROOM</strong><small>PERFORMANCE REVIEW</small></span>
        </div>
        <span className="review-id">REPORT&nbsp; {reviewId.toUpperCase().slice(0, 14)}</span>
      </header>

      <div className="review-page">
        <section className="review-hero">
          <div className="review-hero__copy">
            <span className="eyebrow">INTERVIEW DEBRIEF · 访谈复盘</span>
            <h1>{report.topic}</h1>
            <div className="review-meta">
              <span>嘉宾人设：{report.personaName}</span>
              <i />
              <span>采访时长：{report.duration}</span>
              <i />
              <span>挖出 {foundFacts}/{report.dossier.length} 条料</span>
            </div>
          </div>
          <div className="total-score">
            <span>综合得分</span>
            <strong>{report.total}</strong>
            <small>/ 100</small>
          </div>
        </section>

        <section className="review-overview">
          <article className="review-card radar-card">
            <div className="review-card__heading">
              <div><CircleGauge size={17} /><span>五维能力雷达</span></div>
              <small>按各维度满分归一化</small>
            </div>
            <div className="radar-layout">
              <ScoreRadar dimensions={report.dimensions} />
              <div className="dimension-list">
                {report.dimensions.map((dimension) => (
                  <div key={dimension.name}>
                    <span>{dimension.name}</span>
                    <strong>{dimension.score}<small>/{dimension.max}</small></strong>
                  </div>
                ))}
              </div>
            </div>
          </article>

          <article className="review-card advice-card">
            <div className="review-card__heading">
              <div><Target size={17} /><span>下次上场，先做这三件事</span></div>
            </div>
            <ol>
              {report.advice.map((item, index) => (
                <li key={item}><span>0{index + 1}</span><p>{item}</p></li>
              ))}
            </ol>
            <div className="missed-callout">
              <AlertTriangle size={17} />
              <p><strong>{missedRounds} 轮</strong>导播给出了提示，但你没有照做。</p>
            </div>
          </article>
        </section>

        <section className="session-comparison" aria-labelledby="comparison-title">
          <div className="section-title compact">
            <div>
              <span className="section-index">↗</span>
              <div><span className="eyebrow">SESSION-OVER-SESSION</span><h2 id="comparison-title">对比上一场</h2></div>
            </div>
            <p>同一评分维度直接对照，向上箭头表示本场进步。</p>
          </div>
          {comparison.length > 0 ? (
            <div className="comparison-grid">
              {comparison.map((item) => (
                <article className={`comparison-item is-${item.direction}`} key={item.name}>
                  <span>{item.name}</span>
                  <div>
                    <small>{item.previous}</small>
                    {item.direction === "up" ? <ArrowUp size={15} /> : item.direction === "down" ? <ArrowDown size={15} /> : <Minus size={15} />}
                    <strong>{item.current}</strong>
                  </div>
                  <em>{item.delta > 0 ? `+${item.delta}` : item.delta} 分</em>
                </article>
              ))}
            </div>
          ) : (
            <div className="comparison-empty">这是第一场训练；完成下一场后会在这里显示逐项变化。</div>
          )}
        </section>

        <section className="replay-section">
          <div className="section-title">
            <div>
              <span className="section-index">01</span>
              <div><span className="eyebrow">TURN-BY-TURN</span><h2>逐轮回放</h2></div>
            </div>
            <p>把耳返建议和你的现场动作放在同一时间线上。</p>
          </div>

          <div className="replay-list">
            {report.rounds.map((round) => {
              const missed = Boolean(round.director && round.followed === false);
              return (
                <article className={`replay-round${missed ? " is-missed" : ""}`} key={round.round}>
                  <header className="round-header">
                    <div><span>ROUND</span><strong>{String(round.round).padStart(2, "0")}</strong></div>
                    <time>{round.timestamp}</time>
                    {missed ? (
                      <span className="missed-badge"><AlertTriangle size={13} /> 未执行提示</span>
                    ) : round.director && round.followed === true ? (
                      <span className="followed-badge"><Check size={13} /> 已响应耳返</span>
                    ) : round.director ? (
                      <span className="review-id">未自动判定</span>
                    ) : null}
                  </header>

                  <div className="round-dialogue">
                    <div className="round-line host-line">
                      <span>你</span><p>{round.host}</p>
                    </div>
                    <div className="round-line guest-line">
                      <span>嘉宾</span>
                      <div>
                        {round.stageDirection && <em>（{round.stageDirection}）</em>}
                        <p>{round.guest}</p>
                      </div>
                    </div>
                  </div>

                  {round.director && (
                    <div className="action-compare">
                      <div className="director-action">
                        <span><Headphones size={14} /> 当时导播说</span>
                        <p>“{round.director}”</p>
                      </div>
                      <div className="student-action">
                        <span>{round.followed === true ? <Check size={14} /> : round.followed === false ? <X size={14} /> : <Minus size={14} />} 你下一轮实际问了</span>
                        <p>{round.studentAction}</p>
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>

        <section className="review-lower-grid">
          <div className="metrics-section">
            <div className="section-title compact">
              <div><span className="section-index">02</span><div><span className="eyebrow">BY THE NUMBERS</span><h2>客观指标</h2></div></div>
            </div>
            <div className="metrics-table-wrap">
              <table className="metrics-table">
                <thead><tr><th>指标</th><th>你的数据</th><th>理想区间</th><th>状态</th></tr></thead>
                <tbody>
                  {report.metrics.map((metric) => (
                    <tr className={metric.inRange ? "" : "out-of-range"} key={metric.name}>
                      <td>{metric.name}</td>
                      <td><strong>{metric.value}</strong></td>
                      <td>{metric.ideal}</td>
                      <td>{metric.inRange ? <span className="metric-ok"><Check size={13} /> 达标</span> : <span className="metric-bad"><Activity size={13} /> 超出</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="dossier-section">
            <div className="section-title compact">
              <div><span className="section-index">03</span><div><span className="eyebrow">FULL DOSSIER</span><h2>错过的料</h2></div></div>
            </div>
            <details className="dossier-accordion">
              <summary>
                <span className="dossier-summary-icon"><FileWarning size={18} /></span>
                <span><strong>揭晓完整档案</strong><small>复盘阶段才可查看 · {report.dossier.length - foundFacts} 条未挖出</small></span>
                <ChevronDown className="accordion-chevron" size={18} />
              </summary>
              <div className="dossier-list">
                {report.dossier.map((fact) => (
                  <article className={`dossier-fact ${fact.status}`} key={fact.id}>
                    <div className="fact-id-row">
                      <span>{fact.id}</span>
                      <span>料值 {fact.juiciness}</span>
                      <span className="fact-status">{fact.status === "found" ? "已挖出" : "错过"}</span>
                    </div>
                    <p>{fact.content}</p>
                    <div className="unlock-hint"><Lightbulb size={14} /> <span>{fact.unlockHint}</span></div>
                    {(fact.sources ?? []).map((source) => (
                      <a
                        className="fact-source"
                        href={source.url}
                        key={`${fact.id}-${source.url}`}
                        rel="noreferrer"
                        target="_blank"
                      >
                        来源证据：{source.quote}
                      </a>
                    ))}
                  </article>
                ))}
              </div>
            </details>
          </div>
        </section>
      </div>
    </main>
  );
}
