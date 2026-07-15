import {
  ArrowRight,
  CheckCircle2,
  Clock3,
  Database,
  FileSearch,
  LoaderCircle,
  Radio,
  Sparkles,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

import type { ScenarioPreview, SessionSnapshot } from "../types";


async function errorMessage(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail || fallback;
  } catch {
    return fallback;
  }
}

function localStudentId() {
  const key = "newsroom:local-student-id";
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const created = `local-${crypto.randomUUID()}`;
  localStorage.setItem(key, created);
  return created;
}

function ScenarioCard({
  scenario,
  disabled,
  onStart,
}: {
  scenario: ScenarioPreview;
  disabled: boolean;
  onStart: (scenario: ScenarioPreview) => void;
}) {
  return (
    <article className="scenario-card">
      <div className="scenario-card__meta">
        <span>{scenario.persona_name}</span>
        <span>{scenario.facts_total} 条隐藏事实</span>
      </div>
      <h3>{scenario.topic}</h3>
      <p>{scenario.surface_bio}</p>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onStart(scenario)}
      >
        开始真实访谈 <ArrowRight size={16} />
      </button>
    </article>
  );
}

export function LaunchPage() {
  const [topic, setTopic] = useState("");
  const [scenarios, setScenarios] = useState<ScenarioPreview[]>([]);
  const [generated, setGenerated] = useState<ScenarioPreview | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [creatingSession, setCreatingSession] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pipelineReady, setPipelineReady] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/scenarios")
      .then(async (response) => {
        if (!response.ok) throw new Error(await errorMessage(response, "场景加载失败"));
        return response.json() as Promise<ScenarioPreview[]>;
      })
      .then((items) => {
        if (!cancelled) setScenarios(items);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "场景加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingList(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    fetch("/health")
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((payload: { status?: string }) => setPipelineReady(payload.status === "ok"))
      .catch(() => setPipelineReady(false));
  }, []);

  const generate = async (event: FormEvent) => {
    event.preventDefault();
    const normalized = topic.trim();
    if (normalized.length < 3 || generating) return;

    setGenerating(true);
    setGenerated(null);
    setError(null);
    try {
      const response = await fetch("/api/scenarios/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: normalized }),
      });
      if (!response.ok) {
        throw new Error(await errorMessage(response, "场景生成失败"));
      }
      const scenario = (await response.json()) as ScenarioPreview;
      setGenerated(scenario);
      setScenarios((items) => [
        scenario,
        ...items.filter((item) => item.scenario_id !== scenario.scenario_id),
      ]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "场景生成失败");
    } finally {
      setGenerating(false);
    }
  };

  const start = async (scenario: ScenarioPreview) => {
    if (creatingSession) return;
    setCreatingSession(true);
    setError(null);
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario_id: scenario.scenario_id,
          persona_id: scenario.persona_id,
          student_id: localStudentId(),
        }),
      });
      if (!response.ok) {
        throw new Error(await errorMessage(response, "会话创建失败"));
      }
      const snapshot = (await response.json()) as SessionSnapshot;
      sessionStorage.setItem(
        `newsroom:session:${snapshot.id}`,
        JSON.stringify(snapshot),
      );
      window.location.href = `/studio?session=${encodeURIComponent(snapshot.id)}`;
    } catch (reason) {
      setCreatingSession(false);
      setError(reason instanceof Error ? reason.message : "会话创建失败");
    }
  };

  return (
    <main className="launch-shell">
      <header className="launch-topbar">
        <a className="brand" href="/" aria-label="Newsroom 首页">
          <span className="brand-mark">N</span>
          <span><strong>NEWSROOM</strong><small>INTERVIEW LAB</small></span>
        </a>
        <div className="launch-status"><i /> {pipelineReady === null ? "CHECKING PIPELINE" : pipelineReady ? "PIPELINE READY" : "PIPELINE DEGRADED"}</div>
      </header>

      <section className="launch-hero">
        <div className="launch-copy">
          <span className="eyebrow"><Radio size={14} /> AI INTERVIEW TRAINING</span>
          <h1>从一个真实选题，<br />开始一场不可预演的采访。</h1>
          <p>
            系统会实时搜索公开来源，由模型生成隐藏档案和嘉宾人设；采访中的回答、耳返、评分与复盘全部来自本次会话。
          </p>
          <div className="pipeline-strip">
            <span><FileSearch size={15} /> 真实搜索</span>
            <i />
            <span><Sparkles size={15} /> 生成档案</span>
            <i />
            <span><Database size={15} /> 持久化复盘</span>
          </div>
        </div>

        <form className="topic-console" onSubmit={(event) => void generate(event)}>
          <div className="topic-console__heading">
            <div><span>NEW ASSIGNMENT</span><h2>输入采访选题</h2></div>
            <span className="console-live"><i /> {pipelineReady === null ? "正在检查服务配置" : pipelineReady ? "服务配置已就绪" : "服务配置不完整"}</span>
          </div>
          <label htmlFor="topic">你想训练哪一个新闻事件或争议话题？</label>
          <textarea
            id="topic"
            maxLength={200}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="例如：某地公共自行车项目预算翻倍引发质疑"
            rows={4}
            value={topic}
          />
          <div className="topic-console__foot">
            <span>{topic.length}/200</span>
            <button disabled={topic.trim().length < 3 || generating} type="submit">
              {generating ? (
                <><LoaderCircle className="spin" size={17} /> 正在搜索并生成档案</>
              ) : (
                <>生成真实采访场景 <ArrowRight size={17} /></>
              )}
            </button>
          </div>
          {generating && (
            <div className="generation-note">
              <Clock3 size={15} /> 正在检索来源、逐字核验事实并进行独立审稿，开启高强度推理时通常需要 3–6 分钟。
            </div>
          )}
          {error && <div className="launch-error">{error}</div>}
        </form>
      </section>

      {generated && (
        <section className="generated-section">
          <div className="section-title compact">
            <div>
              <span className="section-index"><CheckCircle2 size={18} /></span>
              <div><span className="eyebrow">DOSSIER READY</span><h2>新场景已生成</h2></div>
            </div>
            <p>页面只展示公开资料；隐藏事实将在采访结束后揭晓。</p>
          </div>
          <ScenarioCard
            scenario={generated}
            disabled={creatingSession}
            onStart={(item) => void start(item)}
          />
        </section>
      )}

      <section className="scenario-library">
        <div className="section-title compact">
          <div>
            <span className="section-index">↻</span>
            <div><span className="eyebrow">SCENARIO LIBRARY</span><h2>已生成场景</h2></div>
          </div>
          <p>这些档案来自本地数据库，可以直接重新训练。</p>
        </div>
        {loadingList ? (
          <div className="scenario-empty"><LoaderCircle className="spin" size={18} /> 正在读取场景</div>
        ) : scenarios.length > 0 ? (
          <div className="scenario-grid">
            {scenarios.map((scenario) => (
              <ScenarioCard
                key={scenario.scenario_id}
                scenario={scenario}
                disabled={creatingSession}
                onStart={(item) => void start(item)}
              />
            ))}
          </div>
        ) : (
          <div className="scenario-empty">还没有真实场景，从上方输入第一个采访选题。</div>
        )}
      </section>
    </main>
  );
}
