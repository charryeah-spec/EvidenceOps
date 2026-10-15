from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from evidenceops.agent import EvidenceOpsAgent
from evidenceops.config import PROJECT_ROOT, Settings
from evidenceops.llm import OpenAICompatibleDiagnosisEngine, RuleBasedDiagnosisEngine
from evidenceops.providers.fixture import FixtureProvider
from evidenceops.reporting import write_report

st.set_page_config(page_title="EvidenceOps", page_icon="🛡️", layout="wide")
st.title("EvidenceOps 可解释智能运维控制台")
st.caption("故障检测 → 证据定位 → 根因分析 → 人工审批 → 安全修复 → 恢复验证")


@st.cache_resource
def build_agent(provider_name: str, engine_name: str) -> EvidenceOpsAgent:
    settings = Settings(evidenceops_provider=provider_name)
    if provider_name == "aiopslab":
        from evidenceops.providers.aiopslab import AIOpsLabProvider

        provider = AIOpsLabProvider(settings)
    else:
        provider = FixtureProvider()

    if engine_name == "api":
        if not settings.llm_configured:
            raise ValueError("API 模式需要在 .env 中配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL")
        engine = OpenAICompatibleDiagnosisEngine(settings)
    elif engine_name == "auto" and settings.llm_configured:
        engine = OpenAICompatibleDiagnosisEngine(settings)
    else:
        engine = RuleBasedDiagnosisEngine()
    return EvidenceOpsAgent(provider, settings, engine)


with st.sidebar:
    st.header("运行配置")
    provider_label = st.selectbox("证据源", ["离线 Fixture", "真实 AIOpsLab"])
    provider_name = "aiopslab" if provider_label == "真实 AIOpsLab" else "fixture"
    engine_name = st.selectbox("诊断引擎", ["auto", "api", "rule"])
    if provider_name == "aiopslab":
        st.warning("真实模式会部署应用、注入故障并调用本地 Kubernetes。")

try:
    agent = build_agent(provider_name, engine_name)
except ValueError as exc:
    st.error(str(exc))
    st.stop()
problems = {
    "应用配置错误": "misconfig_app_hotel_res-detection-1",
    "Pod 故障": "pod_failure_hotel_res-detection-1",
    "网络丢包": "network_loss_hotel_res-detection-1",
    "网络延迟": "network_delay_hotel_res-detection-1",
    "正常场景": "noop_detection_hotel_reservation-1",
}

st.subheader("1. 系统状态")
status_cols = st.columns(4)
status_cols[0].metric("Provider", type(agent.provider).__name__)
status_cols[1].metric("诊断引擎", type(agent.engine).__name__)
status_cols[2].metric("Write policy", "Human approval")
status_cols[3].metric("Namespace", agent.settings.evidenceops_namespace)

st.subheader("2. 故障实验")
selected = st.selectbox("场景", list(problems))
problem_id = problems[selected]
col1, col2 = st.columns(2)
if col1.button("注入并诊断", type="primary", use_container_width=True):
    with st.spinner("正在注入故障并收集多源证据..."):
        try:
            st.session_state.report = agent.run(problem_id, mode="diagnose")
            st.session_state.report_agent_key = (provider_name, engine_name)
        except Exception as exc:
            st.error(f"运行失败：{exc}")
has_pending_report = bool(
    st.session_state.get("report")
    and st.session_state.report.approval_status == "pending"
)
if col2.button(
    "批准修复并验证",
    use_container_width=True,
    disabled=not has_pending_report,
):
    with st.spinner("已记录人工批准，正在执行白名单修复并验证..."):
        try:
            if st.session_state.get("report_agent_key") != (provider_name, engine_name):
                raise ValueError("请使用当前 Provider 和诊断引擎重新执行一次诊断")
            st.session_state.report = agent.remediate(
                st.session_state.report,
                approved=True,
            )
        except Exception as exc:
            st.error(f"运行失败：{exc}")

report = st.session_state.get("report")
if report:
    write_report(report, PROJECT_ROOT / "results" / "raw")
    st.subheader("3. Agent 阶段轨迹")
    st.dataframe(pd.DataFrame([{"阶段": item.phase, "摘要": item.summary} for item in report.phase_trace]), use_container_width=True, hide_index=True)

    st.subheader("4. 关键证据")
    st.dataframe(pd.DataFrame([{"ID": item.evidence_id, "类型": item.kind, "服务": item.service, "异常分": item.anomaly_score, "摘要": item.summary} for item in report.evidence]), use_container_width=True, hide_index=True)

    st.subheader("5. 诊断结论")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("检测到故障", "是" if report.detected else "否")
    c2.metric("故障服务", report.faulty_service or "无")
    c3.metric("根因", report.root_cause or "无")
    c4.metric(
        "校准置信度",
        f"{report.confidence:.1%}",
        delta=f"raw {(report.raw_confidence if report.raw_confidence is not None else report.confidence):.1%}",
        delta_color="off",
    )
    c5.metric("诊断状态", "弃权" if report.abstained else "已决策")
    if report.abstained:
        st.warning(report.abstention_reason)
    if report.topology_analysis:
        st.markdown("**拓扑感知服务排名**")
        st.caption(report.topology_analysis.explanation)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "服务": item.service,
                        "直接异常": item.direct_score,
                        "上游传播": item.propagated_score,
                        "总分": item.total_score,
                    }
                    for item in report.topology_analysis.scores
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("6. 修复审批")
    if report.recommended_actions:
        st.json([action.model_dump(mode="json") for action in report.recommended_actions])
        st.info(f"审批状态：{report.approval_status}")
    else:
        st.success("当前无需修复")

    st.subheader("7. 恢复验证")
    if report.verification:
        st.success(report.verification.message) if report.verification.healthy else st.error(report.verification.message)
        st.write(report.verification.checks)
    else:
        st.caption("尚未执行修复，暂无恢复验证结果。")

st.subheader("8. 评估看板")
metrics_path = PROJECT_ROOT / "results" / "metrics-full.json"
if metrics_path.exists():
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    cols = st.columns(6)
    cols[0].metric("课程检测准确率", f"{metrics['course_detection_accuracy']:.1%}")
    cols[1].metric("F1", f"{metrics['f1']:.3f}")
    cols[2].metric("根因准确率", f"{metrics['root_cause_accuracy']:.1%}")
    cols[3].metric("平均响应时间", f"{metrics['avg_response_time_all_seconds']:.3f}s")
    cols[4].metric("覆盖率", f"{metrics.get('coverage', 0):.1%}")
    cols[5].metric("ECE", f"{metrics.get('calibration', {}).get('ece', 0):.3f}")
    figure = PROJECT_ROOT / "results" / "figures" / "confusion-matrix-full.png"
    if figure.exists():
        st.image(str(figure), caption="Detection confusion matrix", width=520)
    reliability = PROJECT_ROOT / "results" / "figures" / "reliability-full.png"
    if reliability.exists():
        st.image(str(reliability), caption="Confidence reliability diagram", width=520)
else:
    st.caption("运行 `python -m evidenceops evaluate` 后显示评估结果。")
