"""ContraVault — Tender ↔ Vendor Compliance & Scoring Engine.

Streamlit web application for document-intelligence-based tender compliance evaluation.
"""
import sys
import os
import json
import logging
from pathlib import Path
from datetime import date, datetime

import streamlit as st
import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config import INTERMEDIATES_DIR, GROQ_MODEL
from utils.pdf_utils import extract_vendor_id_from_filename
from utils.date_utils import parse_date

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("contravault")

# ─────────────────────────────────────────────
# Page Config & Custom CSS
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="ContraVault — Compliance Engine",
    page_icon="🔒",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    /* Global */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* Header gradient */
    .main-header {
        background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
        padding: 2rem 2.5rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        box-shadow: 0 8px 32px rgba(48, 43, 99, 0.3);
    }
    .main-header h1 {
        color: #fff;
        font-weight: 800;
        font-size: 2.2rem;
        margin: 0;
        letter-spacing: -0.5px;
    }
    .main-header p {
        color: rgba(255,255,255,0.7);
        font-size: 1.05rem;
        margin: 0.3rem 0 0 0;
    }

    /* Stat cards */
    .stat-card {
        background: linear-gradient(145deg, #1e1e2e, #2a2a3e);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 14px;
        padding: 1.4rem;
        text-align: center;
        box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        transition: transform 0.2s, box-shadow 0.2s;
    }
    .stat-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.25);
    }
    .stat-card .number {
        font-size: 2.4rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        line-height: 1.2;
    }
    .stat-card .label {
        color: rgba(255,255,255,0.5);
        font-size: 0.85rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-top: 0.3rem;
    }

    /* Status badges */
    .badge-qualified {
        background: linear-gradient(135deg, #00b09b, #96c93d);
        color: #fff;
        padding: 4px 14px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
        display: inline-block;
    }
    .badge-disqualified {
        background: linear-gradient(135deg, #eb3349, #f45c43);
        color: #fff;
        padding: 4px 14px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
        display: inline-block;
    }
    .badge-pending {
        background: linear-gradient(135deg, #f7971e, #ffd200);
        color: #1a1a2e;
        padding: 4px 14px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
        display: inline-block;
    }

    /* Section headers */
    .section-header {
        border-left: 4px solid #667eea;
        padding-left: 1rem;
        margin: 1.5rem 0 1rem 0;
    }

    /* Compliance colors */
    .compliance-full { color: #00b09b; font-weight: 600; }
    .compliance-partial { color: #f7971e; font-weight: 600; }
    .compliance-missing { color: #eb3349; font-weight: 600; }
    .compliance-not-addressed { color: #6c757d; font-weight: 600; }

    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f0c29 0%, #1a1a2e 100%);
    }
    [data-testid="stSidebar"] .stMarkdown p,
    [data-testid="stSidebar"] .stMarkdown label {
        color: rgba(255,255,255,0.85);
    }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Session State Initialization
# ─────────────────────────────────────────────
def init_session_state():
    """Initialize all session state variables."""
    defaults = {
        "stage": "upload",
        "tender_bundle": None,
        "vendor_bundles": {},
        "requirements": None,
        "vendor_evidence": {},
        "technical_scores": None,
        "eligibility_verdicts": None,
        "price_rankings": None,
        "tender_meta": None,
        "schedules": [],
        "llm_client": None,
        "processing": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

init_session_state()


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuration")

    api_key = st.text_input(
        "Groq API Key",
        type="password",
        value=os.environ.get("GROQ_API_KEY", ""),
        help="Your Groq API key. Set GROQ_API_KEY env var to auto-fill."
    )

    tender_opening_date = st.date_input(
        "Tender Opening Date",
        value=date.today(),
        help="Used to check certificate validity. Auto-extracted if possible."
    )

    st.markdown("---")
    st.markdown("### 📊 Pipeline Status")

    stages = [
        ("📄 Upload", st.session_state.tender_bundle is not None),
        ("📋 Requirements", st.session_state.requirements is not None),
        ("🔍 Evidence", len(st.session_state.vendor_evidence) > 0),
        ("⚡ Scoring", st.session_state.technical_scores is not None),
        ("✅ Eligibility", st.session_state.eligibility_verdicts is not None),
        ("💰 Pricing", st.session_state.price_rankings is not None),
    ]
    for label, done in stages:
        icon = "✅" if done else "⬜"
        st.markdown(f"{icon} {label}")

    if st.session_state.llm_client:
        st.markdown("---")
        usage = st.session_state.llm_client.usage_summary
        st.markdown(f"**API Calls:** {usage['total_calls']}")
        st.markdown(f"**Tokens In:** {usage['total_input_tokens']:,}")
        st.markdown(f"**Tokens Out:** {usage['total_output_tokens']:,}")


# ─────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────
st.markdown("""
<div class="main-header">
    <h1>🔒 ContraVault</h1>
    <p>Tender ↔ Vendor Compliance & Scoring Engine</p>
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Tab Layout
# ─────────────────────────────────────────────
tab_upload, tab_requirements, tab_evidence, tab_compliance, tab_eligibility, tab_pricing, tab_export = st.tabs([
    "📄 Upload & Ingest",
    "📋 Requirements",
    "🔍 Vendor Evidence",
    "⚡ Technical Scores",
    "✅ Eligibility",
    "💰 Price Ranking",
    "📦 Export"
])


# ─────────────────────────────────────────────
# Tab 1: Upload & Ingest
# ─────────────────────────────────────────────
with tab_upload:
    st.markdown('<div class="section-header"><h3>Upload Documents</h3></div>', unsafe_allow_html=True)

    input_mode = st.radio(
        "Input Mode",
        options=["PDF", "Structured (JSON)"],
        horizontal=True,
        help="Choose 'PDF' to extract from raw documents, or 'Structured (JSON)' to bypass extraction."
    )
    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        if input_mode == "PDF":
            st.markdown("**📄 Tender Document (PDF)**")
            tender_file = st.file_uploader(
                "Upload tender PDF",
                type=["pdf"],
                key="tender_upload",
                help="Upload the main tender/bid document"
            )
        else:
            st.markdown("**📄 Tender Document (JSON)**")
            tender_file = st.file_uploader(
                "Upload tender JSON",
                type=["json"],
                key="tender_upload_json",
                help="Upload the structured tender data"
            )

    with col2:
        if input_mode == "PDF":
            st.markdown("**📁 Vendor Bid Documents (PDF)**")
            vendor_files = st.file_uploader(
                "Upload vendor PDFs (one or more per vendor)",
                type=["pdf"],
                accept_multiple_files=True,
                key="vendor_upload",
                help="Upload vendor bid PDFs. Vendor ID is derived from filename."
            )
        else:
            st.markdown("**📁 Vendor Bid Documents (JSON)**")
            vendor_files = st.file_uploader(
                "Upload vendor JSONs (one per vendor)",
                type=["json"],
                accept_multiple_files=True,
                key="vendor_upload_json",
                help="Upload structured vendor evidence. Vendor ID is derived from filename."
            )

    if vendor_files:
        st.markdown("**Detected Vendors:**")
        vendor_map = {}
        for vf in vendor_files:
            vid = extract_vendor_id_from_filename(vf.name)
            if vid not in vendor_map:
                vendor_map[vid] = []
            vendor_map[vid].append(vf.name)
        for vid, files in vendor_map.items():
            st.markdown(f"- **{vid}**: {', '.join(files)}")

    st.markdown("---")

    if st.button("🚀 Process Documents", type="primary", disabled=not tender_file, use_container_width=True):
        if not api_key:
            st.error("⚠️ Please provide a Groq API key in the sidebar.")
        elif not tender_file:
            st.error("⚠️ Please upload a tender document.")
        else:
            with st.status("Processing documents...", expanded=True) as status:
                try:
                    from pipeline.engine_technical import score_technical
                    from pipeline.engine_eligibility import evaluate_eligibility
                    from pipeline.engine_price import rank_prices
                    from retrieval.vector_store import VendorVectorStore
                    from llm.client import GroqClient
                    from models.evidence import VendorEvidenceBundle

                    # Initialize LLM client
                    st.write("🔑 Initializing Groq client...")
                    llm = GroqClient(api_key=api_key)
                    st.session_state.llm_client = llm

                    if input_mode == "PDF":
                        from pipeline.ingest import ingest_tender, ingest_vendor_bundle
                        from pipeline.extract_requirements import extract_requirements
                        from pipeline.extract_evidence import extract_evidence

                        # ── Stage A: Ingest ──
                        st.write("📄 **Stage A:** Ingesting tender document...")
                        tender_bytes = tender_file.read()
                        tender_bundle = ingest_tender(tender_file.name, file_bytes=tender_bytes)
                        st.session_state.tender_bundle = tender_bundle
                        st.write(f"   ✅ Extracted {tender_bundle.page_count} pages from tender")

                        # Ingest vendor documents
                        vendor_bundles = {}
                        if vendor_files:
                            # Group files by vendor ID
                            vendor_file_groups = {}
                            for vf in vendor_files:
                                vid = extract_vendor_id_from_filename(vf.name)
                                if vid not in vendor_file_groups:
                                    vendor_file_groups[vid] = []
                                vendor_file_groups[vid].append((vf.name, vf.read()))

                            for vid, file_list in vendor_file_groups.items():
                                st.write(f"   📁 Ingesting vendor: {vid} ({len(file_list)} files)...")
                                bundle = ingest_vendor_bundle(vid, file_bytes_list=file_list)
                                vendor_bundles[vid] = bundle
                                st.write(f"   ✅ {vid}: {bundle.page_count} pages")

                        st.session_state.vendor_bundles = vendor_bundles
                        status.update(label="Stage A complete — Ingestion done", state="running")

                        # ── Stage B: Extract Requirements ──
                        st.write("📋 **Stage B:** Extracting tender requirements...")
                        req_list = extract_requirements(tender_bundle, llm)
                        st.session_state.requirements = req_list
                        st.write(f"   ✅ Extracted {len(req_list.requirements)} requirements")

                        # Load tender meta for schedules
                        meta_path = INTERMEDIATES_DIR / 'tender_meta.json'
                        if meta_path.exists():
                            with open(meta_path, 'r') as f:
                                tender_meta = json.load(f)
                            st.session_state.tender_meta = tender_meta
                            st.session_state.schedules = [
                                s.get('schedule_id', f"Schedule {i+1}")
                                for i, s in enumerate(tender_meta.get('schedules', []))
                            ]
                            # Update tender opening date if auto-extracted
                            if tender_meta.get('tender_opening_date'):
                                auto_date = parse_date(tender_meta['tender_opening_date'])
                                if auto_date:
                                    st.write(f"   📅 Auto-detected tender opening date: {auto_date}")

                        status.update(label="Stage B complete — Requirements extracted", state="running")

                        # ── Stage C: Extract Evidence ──
                        st.write("🔍 **Stage C:** Extracting vendor evidence...")
                        vendor_evidence = {}
                        for vid, bundle in vendor_bundles.items():
                            st.write(f"   🔍 Extracting evidence for: {vid}...")
                            ev_bundle = extract_evidence(vid, bundle, llm)
                            vendor_evidence[vid] = ev_bundle
                            st.write(f"   ✅ {vid}: {len(ev_bundle.evidence)} evidence items")

                        st.session_state.vendor_evidence = vendor_evidence
                        status.update(label="Stage C complete — Evidence extracted", state="running")
                    
                    else:
                        from utils.structured_loader import load_structured_tender, load_structured_vendor
                        
                        # ── Structured Input Bypass ──
                        st.write("📄 **Loading Structured Tender Data...**")
                        tender_bytes = tender_file.read()
                        req_list, tender_meta = load_structured_tender(tender_bytes, tender_file.name)
                        st.session_state.requirements = req_list
                        st.session_state.tender_meta = tender_meta
                        st.session_state.schedules = [
                            s.get('id', f"Schedule {i+1}")
                            for i, s in enumerate(tender_meta.get('schedules', []))
                        ]
                        if tender_meta.get('tender_opening_date'):
                            auto_date = parse_date(tender_meta['tender_opening_date'])
                            if auto_date:
                                st.write(f"   📅 Using provided tender opening date: {auto_date}")
                        st.write(f"   ✅ Loaded {len(req_list.requirements)} requirements")
                        
                        st.write("🔍 **Loading Structured Vendor Data...**")
                        vendor_evidence = {}
                        if vendor_files:
                            for vf in vendor_files:
                                vid = extract_vendor_id_from_filename(vf.name)
                                st.write(f"   📁 Loading vendor: {vid}...")
                                ev_bundle = load_structured_vendor(vid, vf.read(), vf.name)
                                vendor_evidence[vid] = ev_bundle
                                st.write(f"   ✅ {vid}: {len(ev_bundle.evidence)} evidence items")
                        st.session_state.vendor_evidence = vendor_evidence
                        status.update(label="Data loaded via structured bypass", state="running")

                    # ── Stage D: Scoring ──
                    # Engine 1: Technical scoring
                    st.write("⚡ **Engine 1:** Technical compliance scoring...")
                    vector_store = VendorVectorStore()
                    for vid, ev_bundle in vendor_evidence.items():
                        vector_store.index_evidence(vid, ev_bundle.evidence)

                    tech_scores = score_technical(
                        req_list, vendor_evidence, vector_store, llm,
                        schedules=st.session_state.schedules or None
                    )
                    st.session_state.technical_scores = tech_scores
                    st.write(f"   ✅ Generated {len(tech_scores)} compliance scores")

                    # Engine 2: Eligibility gate
                    st.write("✅ **Engine 2:** Eligibility gate (deterministic)...")
                    elig_verdicts = evaluate_eligibility(
                        req_list, vendor_evidence, tender_opening_date
                    )
                    st.session_state.eligibility_verdicts = elig_verdicts
                    for vid, verdict in elig_verdicts.items():
                        status_badge = "✅ Qualified" if verdict.is_qualified else "❌ Disqualified"
                        st.write(f"   {status_badge}: {vid}")

                    # Engine 3: Price ranking (awaiting quotes initially)
                    st.write("💰 **Engine 3:** Price ranking...")
                    price_rankings = rank_prices(
                        elig_verdicts,
                        {},  # No quotes initially
                        st.session_state.schedules or ["Schedule 1"]
                    )
                    st.session_state.price_rankings = price_rankings
                    st.write("   ⏳ Awaiting vendor quotes for price ranking")

                    status.update(label="✅ Pipeline Complete!", state="complete", expanded=False)

                except Exception as e:
                    status.update(label=f"❌ Pipeline Failed: {str(e)[:100]}", state="error")
                    st.error(f"Pipeline error: {e}")
                    logger.exception("Pipeline failed")


# ─────────────────────────────────────────────
# Tab 2: Requirements Review
# ─────────────────────────────────────────────
with tab_requirements:
    st.markdown('<div class="section-header"><h3>Extracted Tender Requirements</h3></div>', unsafe_allow_html=True)

    if st.session_state.requirements:
        req_list = st.session_state.requirements
        reqs = req_list.requirements

        # Stats row
        c1, c2, c3, c4 = st.columns(4)
        tech_count = sum(1 for r in reqs if r.bucket.value == "technical")
        elig_count = sum(1 for r in reqs if r.bucket.value == "eligibility")
        comm_count = sum(1 for r in reqs if r.bucket.value == "commercial")
        gate_count = sum(1 for r in reqs if r.is_gating)

        for col, num, label in [
            (c1, len(reqs), "Total Requirements"),
            (c2, tech_count, "Technical"),
            (c3, elig_count, "Eligibility (Gating)"),
            (c4, comm_count, "Commercial"),
        ]:
            col.markdown(f"""
            <div class="stat-card">
                <div class="number">{num}</div>
                <div class="label">{label}</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("")

        # Filter controls
        bucket_filter = st.selectbox("Filter by bucket:", ["All", "technical", "eligibility", "commercial"])
        schedule_filter = st.selectbox(
            "Filter by schedule:",
            ["All"] + list(set(r.schedule for r in reqs if r.schedule))
        )

        # Build DataFrame
        rows = []
        for r in reqs:
            if bucket_filter != "All" and r.bucket.value != bucket_filter:
                continue
            if schedule_filter != "All" and r.schedule != schedule_filter:
                continue
            rows.append({
                "ID": r.req_id,
                "Schedule": r.schedule or "All",
                "Bucket": r.bucket.value,
                "Requirement": r.text[:120] + ("..." if len(r.text) > 120 else ""),
                "Expected": str(r.expected)[:60],
                "Gating": "⚠️ YES" if r.is_gating else "No",
                "Source": f"{r.source_ref.file} p.{r.source_ref.page}",
            })

        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, height=500)
        else:
            st.info("No requirements match the current filter.")
    else:
        st.info("📄 Upload and process documents to see extracted requirements.")


# ─────────────────────────────────────────────
# Tab 3: Vendor Evidence
# ─────────────────────────────────────────────
with tab_evidence:
    st.markdown('<div class="section-header"><h3>Vendor Evidence</h3></div>', unsafe_allow_html=True)

    if st.session_state.vendor_evidence:
        vendor_tabs = st.tabs(list(st.session_state.vendor_evidence.keys()))

        for vendor_tab, (vid, ev_bundle) in zip(vendor_tabs, st.session_state.vendor_evidence.items()):
            with vendor_tab:
                # Evidence type summary
                type_counts = {}
                for ev in ev_bundle.evidence:
                    t = ev.evidence_type.value
                    type_counts[t] = type_counts.get(t, 0) + 1

                cols = st.columns(len(type_counts) if type_counts else 1)
                for col, (etype, count) in zip(cols, type_counts.items()):
                    color = "#eb3349" if etype == "prior_contract_price" else "#667eea"
                    col.markdown(f"""
                    <div class="stat-card">
                        <div class="number" style="background: {color}; -webkit-background-clip: text; -webkit-text-fill-color: transparent;">{count}</div>
                        <div class="label">{etype.replace('_', ' ')}</div>
                    </div>
                    """, unsafe_allow_html=True)

                st.markdown("")

                # Evidence table
                rows = []
                for ev in ev_bundle.evidence:
                    rows.append({
                        "Field": ev.field,
                        "Value": str(ev.value)[:100],
                        "Type": ev.evidence_type.value,
                        "Valid Until": str(ev.valid_until) if ev.valid_until else "—",
                        "Source": f"{ev.source_ref.file} p.{ev.source_ref.page}",
                    })

                if rows:
                    df = pd.DataFrame(rows)

                    # Highlight prior_contract_price rows
                    def highlight_type(row):
                        if row["Type"] == "prior_contract_price":
                            return ["background-color: rgba(235, 51, 73, 0.15)"] * len(row)
                        elif row["Type"] == "current_quote":
                            return ["background-color: rgba(0, 176, 155, 0.15)"] * len(row)
                        return [""] * len(row)

                    styled = df.style.apply(highlight_type, axis=1)
                    st.dataframe(styled, use_container_width=True, height=400)
                else:
                    st.info("No evidence extracted for this vendor.")
    else:
        st.info("🔍 Upload and process documents to see vendor evidence.")


# ─────────────────────────────────────────────
# Tab 4: Technical Scores
# ─────────────────────────────────────────────
with tab_compliance:
    st.markdown('<div class="section-header"><h3>Technical Compliance Scores</h3></div>', unsafe_allow_html=True)

    if st.session_state.technical_scores:
        scores = st.session_state.technical_scores

        # Filters
        col1, col2 = st.columns(2)
        vendors = list(set(s.vendor_id for s in scores))
        schedules_in_scores = list(set(s.schedule for s in scores))

        with col1:
            vendor_filter = st.selectbox("Vendor:", ["All"] + vendors, key="score_vendor")
        with col2:
            schedule_filter2 = st.selectbox("Schedule:", ["All"] + schedules_in_scores, key="score_schedule")

        # Build table
        rows = []
        for s in scores:
            if vendor_filter != "All" and s.vendor_id != vendor_filter:
                continue
            if schedule_filter2 != "All" and s.schedule != schedule_filter2:
                continue

            compliance_icon = {
                "full": "🟢",
                "partial": "🟡",
                "missing": "🔴",
                "not_addressed": "⚫",
            }

            rows.append({
                "Vendor": s.vendor_id,
                "Schedule": s.schedule,
                "Req ID": s.req_id,
                "Compliance": f"{compliance_icon.get(s.compliance.value, '❓')} {s.compliance.value}",
                "Score": s.score,
                "Reason": s.reason[:150] + ("..." if len(s.reason) > 150 else ""),
                "Req Source": f"{s.requirement_source_ref.file} p.{s.requirement_source_ref.page}",
                "Evidence Source": (
                    f"{s.evidence_source_ref.file} p.{s.evidence_source_ref.page}"
                    if s.evidence_source_ref else "—"
                ),
            })

        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, height=500)

            # Summary stats
            avg_score = sum(r["Score"] for r in rows) / len(rows) if rows else 0
            full_count = sum(1 for r in rows if "full" in r["Compliance"])
            missing_count = sum(1 for r in rows if "missing" in r["Compliance"] or "not_addressed" in r["Compliance"])

            c1, c2, c3 = st.columns(3)
            c1.metric("Average Score", f"{avg_score:.1f}/100")
            c2.metric("Full Compliance", full_count)
            c3.metric("Missing/Not Addressed", missing_count)
        else:
            st.info("No scores match the current filter.")
    else:
        st.info("⚡ Upload and process documents to see technical scores.")


# ─────────────────────────────────────────────
# Tab 5: Eligibility
# ─────────────────────────────────────────────
with tab_eligibility:
    st.markdown('<div class="section-header"><h3>Eligibility Verdicts</h3></div>', unsafe_allow_html=True)

    if st.session_state.eligibility_verdicts:
        verdicts = st.session_state.eligibility_verdicts

        for vid, verdict in verdicts.items():
            with st.container():
                badge = (
                    '<span class="badge-qualified">✅ QUALIFIED</span>'
                    if verdict.is_qualified
                    else '<span class="badge-disqualified">❌ DISQUALIFIED</span>'
                )
                st.markdown(f"### {vid} {badge}", unsafe_allow_html=True)

                if verdict.failed_conditions:
                    st.error("**Failed Conditions:**")
                    for fc in verdict.failed_conditions:
                        st.markdown(f"- ❌ {fc}")

                # Show all checks
                with st.expander(f"All gating checks ({len(verdict.checks)} total)", expanded=False):
                    for check in verdict.checks:
                        icon = "✅" if check.passed else "❌"
                        source = (
                            f" (source: {check.evidence_used.file} p.{check.evidence_used.page})"
                            if check.evidence_used else ""
                        )
                        st.markdown(f"{icon} **{check.requirement_id}**: {check.reason}{source}")

                st.markdown("---")
    else:
        st.info("✅ Upload and process documents to see eligibility verdicts.")


# ─────────────────────────────────────────────
# Tab 6: Price Ranking
# ─────────────────────────────────────────────
with tab_pricing:
    st.markdown('<div class="section-header"><h3>Price Ranking & L1/L2 Determination</h3></div>', unsafe_allow_html=True)

    if st.session_state.eligibility_verdicts:
        verdicts = st.session_state.eligibility_verdicts
        schedules = st.session_state.schedules or ["Schedule 1"]

        st.markdown("#### Enter Current Quotes")
        st.caption("⚠️ Enter ONLY current bid prices. Historical prices from past contracts must NOT be used.")

        # Quote entry form
        quotes = {}
        qualified_vendors = [
            vid for vid, v in verdicts.items() if v.is_qualified
        ]

        if qualified_vendors:
            quote_cols = st.columns(len(schedules) + 1)
            quote_cols[0].markdown("**Vendor**")
            for i, sched in enumerate(schedules):
                quote_cols[i + 1].markdown(f"**{sched}**")

            for vid in qualified_vendors:
                quote_row = st.columns(len(schedules) + 1)
                quote_row[0].markdown(f"**{vid}**")
                quotes[vid] = {}
                for i, sched in enumerate(schedules):
                    price = quote_row[i + 1].number_input(
                        f"{vid} {sched}",
                        min_value=0.0,
                        value=0.0,
                        step=100.0,
                        key=f"quote_{vid}_{sched}",
                        label_visibility="collapsed"
                    )
                    if price > 0:
                        quotes[vid][sched] = price

            if st.button("📊 Calculate Rankings", type="primary", use_container_width=True):
                from pipeline.engine_price import rank_prices

                price_results = rank_prices(verdicts, quotes, schedules)
                st.session_state.price_rankings = price_results

        # Display rankings
        if st.session_state.price_rankings:
            st.markdown("---")
            st.markdown("#### Rankings by Schedule")

            for spr in st.session_state.price_rankings:
                st.markdown(f"##### {spr.schedule}")

                if spr.h1_vendor:
                    st.caption(f"⚠️ H1 (highest-priced, eliminated from RA): {spr.h1_vendor}")

                rows = []
                for r in sorted(spr.rankings, key=lambda x: (x.rank or 999)):
                    status_badge = {
                        "ranked": f"L{r.rank}" if r.rank else "—",
                        "pending_quote": "⏳ Pending",
                        "disqualified": "❌ DQ",
                        "awaiting_quotes": "⏳ Awaiting",
                    }
                    rows.append({
                        "Rank": status_badge.get(r.status.value, r.status.value),
                        "Vendor": r.vendor_id,
                        "Price": f"₹{r.price:,.2f}" if r.price else "—",
                        "Status": r.status.value,
                        "H1 Eliminated": "⚠️ Yes" if r.h1_eliminated else "",
                    })

                if rows:
                    df = pd.DataFrame(rows)
                    st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("💰 Upload and process documents first, then enter quotes for ranking.")


# ─────────────────────────────────────────────
# Tab 7: Export
# ─────────────────────────────────────────────
with tab_export:
    st.markdown('<div class="section-header"><h3>Export Results</h3></div>', unsafe_allow_html=True)

    if st.session_state.technical_scores:
        col1, col2, col3 = st.columns(3)

        # Export compliance scores as CSV
        with col1:
            scores_data = []
            for s in st.session_state.technical_scores:
                scores_data.append({
                    "vendor_id": s.vendor_id,
                    "schedule": s.schedule,
                    "req_id": s.req_id,
                    "compliance": s.compliance.value,
                    "score": s.score,
                    "reason": s.reason,
                    "requirement_source": f"{s.requirement_source_ref.file} p.{s.requirement_source_ref.page}",
                    "evidence_source": (
                        f"{s.evidence_source_ref.file} p.{s.evidence_source_ref.page}"
                        if s.evidence_source_ref else ""
                    ),
                })
            df_scores = pd.DataFrame(scores_data)
            csv_scores = df_scores.to_csv(index=False)
            st.download_button(
                "📥 Download Compliance Scores (CSV)",
                csv_scores,
                "contravault_compliance_scores.csv",
                "text/csv",
                use_container_width=True
            )

        # Export eligibility verdicts as JSON
        with col2:
            if st.session_state.eligibility_verdicts:
                elig_data = {}
                for vid, v in st.session_state.eligibility_verdicts.items():
                    elig_data[vid] = v.model_dump(mode='json')
                json_elig = json.dumps(elig_data, indent=2, ensure_ascii=False)
                st.download_button(
                    "📥 Download Eligibility Verdicts (JSON)",
                    json_elig,
                    "contravault_eligibility.json",
                    "application/json",
                    use_container_width=True
                )

        # Export all intermediates as ZIP
        with col3:
            if INTERMEDIATES_DIR.exists():
                intermediate_files = list(INTERMEDIATES_DIR.glob("*.json"))
                if intermediate_files:
                    import io
                    import zipfile
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for fp in intermediate_files:
                            zf.write(fp, fp.name)
                    buffer.seek(0)
                    st.download_button(
                        "📥 Download All Intermediates (ZIP)",
                        buffer,
                        "contravault_intermediates.zip",
                        "application/zip",
                        use_container_width=True
                    )
    else:
        st.info("📦 Process documents first to enable exports.")


# ─────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────
st.markdown("---")
st.markdown(
    """<div style="text-align:center; color: rgba(255,255,255,0.3); font-size:0.8rem;">
    ContraVault v1.0 — Tender ↔ Vendor Compliance & Scoring Engine<br>
    Three engines: Technical (LLM) · Eligibility (Deterministic) · Price (Numeric)
    </div>""",
    unsafe_allow_html=True
)
